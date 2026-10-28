#!/usr/bin/env python3
"""Aggregate measurement runs into the comparison table for chapter 4.4.

Reads the run_*.json records written by measure.py, groups them by configuration
label, and reports the median across repetitions together with the min-max spread,
rather than a single number from a single run.

    python aggregate.py                       # summarise ../data
    python aggregate.py --out summary.csv     # also write the table as CSV
    python aggregate.py --data ../data/sweep --drop-first   # first run after each recreate out

With exactly two configurations, a baseline (cache_off or png) and a treatment, it also
pairs the runs by seed (run_ab.sh gives both runs of a pair the same one) and reports the
paired differences as KUVIOT_JA_TAULUKOT.md, "Esitystapa", fixes them: median (min-max)
and n, the exact two-sided Wilcoxon signed-rank test, and as a sensitivity check the
paired t-test with a 95 % confidence interval for the mean difference.
"""

import argparse
import csv
import itertools
import json
import math
import statistics
from pathlib import Path

METRICS = [
    ("throughput_rps", "Throughput [req/s]", lambda r: r["throughput_rps"]),
    ("median_ms", "Latency median [ms]", lambda r: r["latency_ms"]["median"]),
    ("p95_ms", "Latency p95 [ms]", lambda r: r["latency_ms"]["p95"]),
    ("p99_ms", "Latency p99 [ms]", lambda r: r["latency_ms"]["p99"]),
    ("hit_rate", "Cache hit rate", lambda r: r["cache"]["hit_rate"]),
    ("misses", "Misses (renders)", lambda r: r["cache"]["misses"]),
    ("evictions", "Evictions", lambda r: r["cache"]["evictions"]),
    ("package_watts", "Package power [W]", lambda r: r.get("package_watts")),
    ("idle_package_watts", "Idle package power [W]", lambda r: r.get("idle_package_watts")),
    ("joules_per_request", "Energy [J/req]", lambda r: r["joules_per_request"]),
    ("joules_per_request_above_idle", "Energy above idle [J/req]",
     lambda r: r.get("joules_per_request_above_idle")),
    ("response_kib", "Response size [KiB]",
     lambda r: r["response_bytes"]["mean"] / 1024
     if (r.get("response_bytes") or {}).get("mean") is not None else None),
    ("network_mbit_s", "Network [Mbit/s]", lambda r: r.get("network_mbit_s")),
]

# Coefficient of variation (stdev / mean) across repetitions, per configuration. The noise
# check decides the number of pairs from the first one before the series starts; see
# KUVIOT_JA_TAULUKOT.md section 3. Energy above idle is the difference of two measured
# values, so its CV runs higher and is reported without a limit.
CV_METRICS = [
    ("joules_per_request", "Energy CV [%]"),
    ("joules_per_request_above_idle", "Energy above idle CV [%]"),
]
CV_LIMIT_PERCENT = 15.0

# The paired comparison runs treatment minus baseline, for the two energy figures the test
# was chosen for in advance (KUVIOT_JA_TAULUKOT.md, "Esitystapa").
BASELINES = ("cache_off", "png")
PAIRED_METRICS = [
    ("joules_per_request", "Energy [J/req]"),
    ("joules_per_request_above_idle", "Energy above idle [J/req]"),
]

# Conditions that must be identical across every run in a comparison, and where each one
# is recorded. A difference in any of them is a confound, not an effect.
SHARED_CONDITIONS = {
    "commit": lambda run: run.get("commit"),
    "turbo disabled (no_turbo)": lambda run: power(run).get("no_turbo"),
    "CPU governor": lambda run: power(run).get("governor"),
    "energy performance preference": lambda run: power(run).get("energy_performance_preference"),
    "image service build (image id)": lambda run: (run.get("service_container") or {}).get("image_id"),
    "request rate": lambda run: run["config"].get("target_rps"),
    "load processes x threads": lambda run: (run["config"].get("processes"),
                                             run["config"].get("concurrency")),
    "measured window [s]": lambda run: run["config"].get("duration_s"),
    "Zipf alpha": lambda run: run["load_profile"].get("alpha"),
    "image size": lambda run: run["config"].get("image_size"),
}


def power(run):
    state = (run.get("sut_state") or {}).get("window_start") or {}
    return state.get("power") or {}


def package_temperature(run, moment):
    state = (run.get("sut_state") or {}).get(moment) or {}
    return (state.get("temperatures_c") or {}).get("x86_pkg_temp")


def check_conditions(runs):
    """Return warnings about runs whose conditions differ where they must not.

    Only fields a run actually recorded are compared, so older records without them are
    not flagged for their absence here; missing provenance is reported separately.
    """
    warnings = []
    for name, extract in SHARED_CONDITIONS.items():
        values = {repr(extract(run)) for run in runs if extract(run) is not None}
        if len(values) > 1:
            warnings.append(f"{name} differs between runs: {', '.join(sorted(values))}")

    by_label = {}
    for run in runs:
        options = (run.get("service_container") or {}).get("catalina_opts")
        if options is not None:
            by_label.setdefault(run["label"], set()).add(options)
    for label, options in sorted(by_label.items()):
        if len(options) > 1:
            warnings.append(f"{label}: JVM options (cache flag) differ between runs: {sorted(options)}")

    for run in runs:
        name = f"{run['label']} {run['timestamp'][:19]}"
        if run.get("commit_dirty"):
            warnings.append(f"{name}: made with modified files {run.get('modified_files')}")
        if power(run).get("ac_online") is False:
            warnings.append(f"{name}: SUT was on battery")
        battery = ((run.get("sut_state") or {}).get("window_start") or {}).get("battery") or {}
        if battery.get("status") in ("Charging", "Discharging"):
            warnings.append(f"{name}: battery was {battery['status'].lower()} "
                            f"at {battery.get('capacity_percent')} %")
        if run.get("energy_source") == "rapl-powercap-remote" and not run.get("service_container"):
            warnings.append(f"{name}: no record of which image build was measured")
    return warnings


def load_runs(data_dir):
    runs = []
    for path in sorted(data_dir.glob("run_*.json")):
        if path.name.endswith("_latencies.csv"):
            continue
        runs.append(json.loads(path.read_text()))
    return runs


def summarise(runs):
    grouped = {}
    for run in runs:
        grouped.setdefault(run["label"], []).append(run)

    summary = {}
    for label, label_runs in grouped.items():
        row = {"repetitions": len(label_runs), "cv": {}}
        for key, _, extract in METRICS:
            values = [extract(run["results"]) for run in label_runs]
            values = [value for value in values if value is not None]
            if values:
                row[key] = (statistics.median(values), min(values), max(values))
            if key in dict(CV_METRICS) and len(values) > 1 and statistics.mean(values) > 0:
                row["cv"][key] = 100 * statistics.stdev(values) / statistics.mean(values)
        summary[label] = row
    return summary


def pair_runs(runs):
    """Pair the runs of a two-configuration comparison by seed.

    Returns (baseline, treatment, [(baseline run, treatment run)]), or None when the runs
    are not a baseline-treatment comparison or a seed does not hold one run of each.
    """
    labels = sorted({run["label"] for run in runs})
    baselines = [label for label in labels if label in BASELINES]
    if len(labels) != 2 or len(baselines) != 1:
        return None
    baseline = baselines[0]
    treatment = next(label for label in labels if label != baseline)
    by_seed = {}
    for run in runs:
        by_seed.setdefault(run["config"].get("seed"), {}).setdefault(run["label"], []).append(run)
    pairs = []
    for seed in sorted(by_seed, key=str):
        both = by_seed[seed]
        if seed is None or len(both.get(baseline, [])) != 1 or len(both.get(treatment, [])) != 1:
            return None
        pairs.append((both[baseline][0], both[treatment][0]))
    return baseline, treatment, pairs


def wilcoxon_exact(differences):
    """Exact two-sided p-value of the Wilcoxon signed-rank test.

    Zero differences are dropped and tied absolute differences share their mean rank,
    as is usual; the null distribution of W+ is then enumerated over every sign pattern,
    which is exact for any number of pairs a session can produce.
    """
    nonzero = [d for d in differences if d != 0]
    n = len(nonzero)
    if n == 0:
        return 1.0
    order = sorted(range(n), key=lambda i: abs(nonzero[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(nonzero[order[j + 1]]) == abs(nonzero[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    # Ranks are whole or half numbers, so doubled they count exactly as integers.
    doubled = [round(2 * rank) for rank in ranks]
    observed = sum(r for r, d in zip(doubled, nonzero) if d > 0)
    counts = {0: 1}
    for rank in doubled:
        step = {}
        for total, ways in counts.items():
            step[total] = step.get(total, 0) + ways
            step[total + rank] = step.get(total + rank, 0) + ways
        counts = step
    patterns = 2 ** n
    lower = sum(ways for total, ways in counts.items() if total <= observed) / patterns
    upper = sum(ways for total, ways in counts.items() if total >= observed) / patterns
    return min(1.0, 2 * min(lower, upper))


def regularized_beta(x, a, b):
    """I_x(a, b) by the continued fraction of Numerical Recipes (betacf)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    if x > (a + 1) / (a + b + 2):
        return 1.0 - regularized_beta(1 - x, b, a)
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log(1 - x)) / a
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1)
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    result = d
    for m in range(1, 300):
        for numerator in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
                          -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
            c = 1.0 + numerator / c
            c = c if abs(c) > 1e-300 else 1e-300
            result *= d * c
        if abs(d * c - 1.0) < 1e-12:
            break
    return front * result


def t_two_sided_p(t, df):
    return regularized_beta(df / (df + t * t), df / 2, 0.5)


def t_critical(df, confidence=0.95):
    """The t value whose two-sided tail probability is 1 - confidence, by bisection."""
    low, high = 0.0, 1000.0
    for _ in range(200):
        middle = (low + high) / 2
        if t_two_sided_p(middle, df) > 1 - confidence:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def paired_statistics(pairs, key):
    """Differences treatment - baseline, absolute and relative to each pair's baseline."""
    values = [(base["results"].get(key), treated["results"].get(key)) for base, treated in pairs]
    values = [(b, t) for b, t in values if b is not None and t is not None]
    n = len(values)
    if n < 2:
        return None
    differences = [t - b for b, t in values]
    percents = [100 * (t - b) / b for b, t in values]
    baseline_mean = statistics.mean(b for b, _ in values)
    mean = statistics.mean(differences)
    stdev = statistics.stdev(differences)
    half_width = t_critical(n - 1) * stdev / math.sqrt(n)
    t = mean / (stdev / math.sqrt(n)) if stdev > 0 else math.inf
    return {
        "n": n,
        "median": statistics.median(differences),
        "min": min(differences),
        "max": max(differences),
        "median_percent": statistics.median(percents),
        "min_percent": min(percents),
        "max_percent": max(percents),
        "mean": mean,
        "ci": (mean - half_width, mean + half_width),
        "ci_percent": (100 * (mean - half_width) / baseline_mean,
                       100 * (mean + half_width) / baseline_mean),
        "mean_percent": 100 * mean / baseline_mean,
        "wilcoxon_p": wilcoxon_exact(differences),
        "t_p": t_two_sided_p(t, n - 1) if math.isfinite(t) else 0.0,
        "same_sign": all(d < 0 for d in differences) or all(d > 0 for d in differences),
    }


def instance_note(runs, label):
    """Say how many JVM instances the runs of a label came from, where that was recorded."""
    containers = [run.get("service_container") or {} for run in runs if run["label"] == label]
    if not all(c.get("id") for c in containers):
        return f"{label}: JVM instance not recorded for every run (records before 2.10.2026)"
    # Fewer instances than runs is not missing data: those runs shared a JVM, which in a
    # --fresh-ssh series means a recreate did not happen.
    ids = {(c["id"], c.get("started_at")) for c in containers}
    return f"{label}: {len(ids)} JVM instance(s) over {len(containers)} runs"


def check_interleaved(runs):
    """Warn if every run of one label finished before the next label started."""
    ordered = sorted(runs, key=lambda run: run["timestamp"])
    sequence = [run["label"] for run in ordered]
    switches = sum(1 for a, b in zip(sequence, sequence[1:]) if a != b)
    distinct = len(set(sequence))
    return distinct > 1 and switches <= distinct - 1


def format_cell(stats):
    if stats is None:
        return "-"
    median, low, high = stats
    if isinstance(median, float):
        return f"{median:.3g} ({low:.3g}-{high:.3g})"
    return f"{median} ({low}-{high})"


def format_cv(cv):
    return "-" if cv is None else f"{cv:.1f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(Path(__file__).resolve().parent.parent / "data"))
    parser.add_argument("--out", help="optional path for a CSV copy of the table")
    parser.add_argument("--drop-first", action="store_true",
                        help="leave out each configuration's earliest run; for the budget sweep, "
                             "where it follows a container recreate (SETUP.md, D5)")
    args = parser.parse_args()

    runs = load_runs(Path(args.data))
    if not runs:
        parser.error(f"no run_*.json found in {args.data}")
    if args.drop_first:
        first = {}
        for run in sorted(runs, key=lambda run: run["timestamp"]):
            first.setdefault(run["label"], run["timestamp"])
        runs = [run for run in runs if run["timestamp"] != first[run["label"]]]
        print(f"Left out the first run of each of {len(first)} configurations.\n")

    if any(run.get("mock") for run in runs):
        print("!" * 78)
        print("! This summary includes SYNTHETIC energy values from a mock dry run.")
        print("! Pipeline validation only - do not report these as measurements.")
        print("!" * 78 + "\n")

    for warning in check_conditions(runs):
        print(f"WARNING: {warning}")

    temperatures = [(package_temperature(run, "window_start"), package_temperature(run, "window_end"))
                    for run in runs]
    temperatures = [pair for pair in temperatures if None not in pair]
    if temperatures:
        starts, ends = zip(*temperatures)
        print(f"CPU package temperature: {min(starts):.0f}-{max(starts):.0f} C at window start, "
              f"{min(ends):.0f}-{max(ends):.0f} C at window end\n")

    if check_interleaved(runs):
        print("WARNING: configurations look sequential, not interleaved. Thermal drift\n"
              "         can masquerade as an effect; alternate A/B/A/B for the real runs.\n")

    summary = summarise(runs)
    labels = sorted(summary)
    width = max(len(name) for _, name, _ in METRICS) + 2

    print(f"{'Metric':<{width}}" + "".join(f"{label:>26}" for label in labels))
    print("-" * (width + 26 * len(labels)))
    print(f"{'Repetitions':<{width}}" + "".join(
        f"{summary[label]['repetitions']:>26}" for label in labels))
    for key, name, _ in METRICS:
        cells = "".join(f"{format_cell(summary[label].get(key)):>26}" for label in labels)
        print(f"{name:<{width}}{cells}")
    for key, name in CV_METRICS:
        cells = "".join(f"{format_cv(summary[label]['cv'].get(key)):>26}" for label in labels)
        print(f"{name:<{width}}{cells}")
    print("\nCells show median (min-max) across repetitions; CV is stdev / mean.")

    for label in labels:
        cv = summary[label]["cv"].get("joules_per_request")
        if cv is not None and cv > CV_LIMIT_PERCENT:
            print(f"\nWARNING: {label}: energy CV {cv:.1f} % exceeds {CV_LIMIT_PERCENT:.0f} %. "
                  "In a noise check, fix the conditions\n"
                  "         and repeat it before the series (KUVIOT_JA_TAULUKOT.md, section 3).")

    paired = pair_runs(runs)
    paired_rows = []
    if paired:
        baseline, treatment, pairs = paired
        print(f"\nPaired differences, {treatment} - {baseline}, {len(pairs)} pairs by seed:")
        for key, name in PAIRED_METRICS:
            result = paired_statistics(pairs, key)
            if result is None:
                continue
            paired_rows.append((name, result))
            print(f"  {name}")
            print(f"    median {result['median']:+.3g} ({result['min']:+.3g} to {result['max']:+.3g}), "
                  f"{result['median_percent']:+.2f} % ({result['min_percent']:+.2f} to "
                  f"{result['max_percent']:+.2f} %), n = {result['n']}"
                  f"{', same sign in every pair' if result['same_sign'] else ''}")
            print(f"    Wilcoxon signed-rank, exact two-sided: p = {result['wilcoxon_p']:.4g}")
            print(f"    mean {result['mean']:+.3g}, 95 % CI {result['ci'][0]:+.3g} to {result['ci'][1]:+.3g} "
                  f"({result['mean_percent']:+.2f} %, CI {result['ci_percent'][0]:+.2f} to "
                  f"{result['ci_percent'][1]:+.2f} % of the baseline mean); "
                  f"paired t-test p = {result['t_p']:.4g}")
        for label in (baseline, treatment):
            print(f"  {instance_note(runs, label)}")
        print("  The interval covers the variation between pairs. If each configuration ran on\n"
              "  one JVM instance, that is run-to-run noise only, not the difference between\n"
              "  instances (TESTIPENKKI.md, section 5).")

    if args.out:
        with Path(args.out).open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["metric"] + labels)
            writer.writerow(["repetitions"] + [summary[label]["repetitions"] for label in labels])
            for key, name, _ in METRICS:
                writer.writerow([name] + [format_cell(summary[label].get(key)) for label in labels])
            for key, name in CV_METRICS:
                writer.writerow([name] + [format_cv(summary[label]["cv"].get(key)) for label in labels])
            if paired_rows:
                writer.writerow([])
                writer.writerow(["paired metric", "n", "median diff", "min diff", "max diff",
                                 "median %", "min %", "max %", "mean diff", "CI low", "CI high",
                                 "mean %", "CI low %", "CI high %", "Wilcoxon p", "t-test p"])
                for name, r in paired_rows:
                    writer.writerow([name, r["n"], r["median"], r["min"], r["max"],
                                     r["median_percent"], r["min_percent"], r["max_percent"],
                                     r["mean"], r["ci"][0], r["ci"][1], r["mean_percent"],
                                     r["ci_percent"][0], r["ci_percent"][1],
                                     r["wilcoxon_p"], r["t_p"]])
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()

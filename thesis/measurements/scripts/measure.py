#!/usr/bin/env python3
"""Service-level measurement driver for the rendered-image cache evaluation.

Runs one configuration (cache on or off) against a target image service: a warm-up
phase that is discarded, then a measured window during which per-request latencies,
CPU package energy and the service's own cache counters are recorded.

Energy comes from the Intel/AMD RAPL powercap interface on Linux. On any other
platform, or when the counters are unreadable, the run is marked as synthetic and
the energy numbers are fabricated for pipeline testing only.

Load is closed-loop: --concurrency workers each issue requests back-to-back, so
throughput is an outcome of the run rather than an input to it.

Example, mock dry run:
    python stub_image_service.py --cache off --port 8090
    python measure.py --label cache_off --duration 20 --energy synthetic

Example, real run from a separate load driver, with rapl_server.py running on the SUT:
    python measure.py --label cache_off --target http://<sut>:8082/tools.descartes.teastore.image \\
        --energy-url http://<sut>:9100 --duration 120 --warmup 30
"""

import argparse
import bisect
import csv
import functools
import json
import multiprocessing
import os
import platform
import queue
import random
import socket
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

RAPL_ROOT = Path("/sys/class/powercap")
SYNTHETIC_IDLE_WATTS = 6.0
SYNTHETIC_JOULES_PER_RENDER = 0.045
SYNTHETIC_JOULES_PER_HIT = 0.002


@functools.lru_cache(maxsize=8)
def zipf_cumulative(catalog_size, alpha):
    """Cumulative distribution over rank-ordered popularity, weight(rank) proportional to rank**-alpha."""
    weights = [rank ** -alpha for rank in range(1, catalog_size + 1)]
    total = sum(weights)
    cumulative = []
    running = 0.0
    for weight in weights:
        running += weight / total
        cumulative.append(running)
    return tuple(cumulative)


def top_share(catalog_size, alpha, fraction=0.2):
    """Share of requests drawn by the most popular `fraction` of the catalog."""
    cumulative = zipf_cumulative(catalog_size, alpha)
    return cumulative[max(int(fraction * catalog_size) - 1, 0)]


def pick_product(catalog_size, rng, alpha):
    """Choose which product image the next request asks for.

    Requests follow a Zipf-like popularity distribution, the profile repeatedly
    observed in web proxy traces by Breslau et al. (1999), who measured alpha
    between 0.64 and 0.83 and showed it implies cache hit rate growing
    logarithmically with cache size. Returns the popularity rank, 0 being the most
    requested; run_load maps rank r to product first_product_id + r. The image
    service's cost does not depend on the id, so this ordering does not bias the result.
    """
    cumulative = zipf_cumulative(catalog_size, alpha)
    index = bisect.bisect_left(cumulative, rng.random())
    return min(index, catalog_size - 1)


def read_rapl_zones():
    """Return {zone_name: (energy_uj_path, max_range_uj)} for readable RAPL zones."""
    zones = {}
    if not RAPL_ROOT.is_dir():
        return zones
    for zone in sorted(RAPL_ROOT.glob("intel-rapl:*")):
        energy_file = zone / "energy_uj"
        try:
            energy_file.read_text()
            name = (zone / "name").read_text().strip()
            max_range = int((zone / "max_energy_range_uj").read_text())
        except (OSError, ValueError):
            continue
        zones[name] = (energy_file, max_range)
    return zones


def read_energy_uj(zones):
    readings = {}
    for name, (energy_file, _) in zones.items():
        try:
            readings[name] = int(energy_file.read_text())
        except OSError:
            pass
    return readings


def fetch_remote_energy(energy_url, timeout=5.0):
    """Read the SUT's counters from rapl_server.py.

    Returns ({name: energy_uj}, {name: max_range_uj}, host, state), where state holds the
    SUT's power settings, temperatures and battery at the moment of this reading.
    """
    with urllib.request.urlopen(f"{energy_url}/energy", timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    zones = payload["zones"]
    return ({name: zone["energy_uj"] for name, zone in zones.items()},
            {name: zone["max_energy_range_uj"] for name, zone in zones.items()},
            payload.get("host"), payload.get("state"))


def fetch_service_container(energy_url, target, timeout=15.0):
    """The SUT container serving `target`, matched by its published port, or None.

    Its image id shows which build was measured, and matching ids across configurations
    show they ran the same one. Its CATALINA_OPTS show the cache flag it started with.
    """
    port = urllib.parse.urlsplit(target).port
    try:
        with urllib.request.urlopen(f"{energy_url}/containers", timeout=timeout) as response:
            running = json.loads(response.read().decode("utf-8"))["containers"]
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError, KeyError):
        return None
    matches = [container for container in running if port in container.get("host_ports", [])]
    return matches[0] if len(matches) == 1 else None


def headline_joules(joules):
    """Energy the J/req figure is based on: the CPU package zones only.

    RAPL zones nest: core and uncore are parts of package-N, and psys (where present)
    covers the whole SoC platform, package included. Summing every zone would count the
    same energy several times, so the headline uses package-N and the other zones are
    kept alongside it in energy_joules for reference.
    """
    packages = [value for name, value in joules.items()
                if name.startswith("package") or name == "synthetic-package"]
    return sum(packages) if packages else None


def energy_delta_joules(max_ranges, before, after):
    """Joules consumed per zone, accounting for counter wraparound."""
    deltas = {}
    for name, start in before.items():
        if name not in after:
            continue
        max_range = max_ranges[name]
        raw = after[name] - start
        if raw < 0:
            raw += max_range
        deltas[name] = raw / 1_000_000.0
    return deltas


CACHE_COUNTERS = {
    "Rendered Image Cache Hits": "hits",
    "Rendered Image Cache Misses": "misses",
    "Rendered Image Cache Evictions": "evictions",
    "Rendered Image Cache Entries": "entries",
}


def fetch_status(target, timeout=5.0):
    """Read the image provider's state endpoint and pull out the rendered-cache counters.

    The endpoint returns text/plain, one "Label: value" pair per line, as assembled by
    SetupController.getState().
    """
    try:
        with urllib.request.urlopen(f"{target}/rest/image/state", timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        return {}

    counters = {}
    for line in text.splitlines():
        label, separator, value = line.partition(":")
        if separator and label.strip() == "Rendered Image Cache Size (Bytes)":
            # "<bytes in use> / <budget>"; the budget shows what the JVM option actually set
            used, _, budget = value.partition("/")
            try:
                counters["bytes"], counters["max_bytes"] = int(used.strip()), int(budget.strip())
            except ValueError:
                pass
            continue
        if not separator or label.strip() not in CACHE_COUNTERS:
            continue
        try:
            counters[CACHE_COUNTERS[label.strip()]] = int(value.strip())
        except ValueError:
            pass
    return counters


def fetch_product_image(target, product_id, size, timeout=10.0):
    body = json.dumps({str(product_id): f"{size[0]}x{size[1]}"}).encode()
    request = urllib.request.Request(
        f"{target}/rest/image/getProductImages", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8")).get(str(product_id))


def check_product_ids(target, first_product_id, catalog_size, size):
    """Return the ids at the ends of the requested range that get the "not found" image.

    The image provider answers an unknown product id with its placeholder image rather
    than an error, so a wrong id range silently turns part of the workload into
    requests for one placeholder. TeaStore's seeded database starts product ids at 7,
    not 0. Id -1 never exists and serves as the reference for the placeholder. Returns
    None if the target could not be queried.
    """
    try:
        placeholder = fetch_product_image(target, -1, size)
        ends = {first_product_id, first_product_id + catalog_size - 1}
        return sorted(pid for pid in ends if fetch_product_image(target, pid, size) == placeholder)
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError):
        return None


def issue_request(target, product_id, size, timeout):
    """POST a single-product image request, the same call the WebUI makes.

    Returns (latency_ms, ok, response_bytes). Response size is recorded because it drives
    the cost of a request: the image is base64 inside JSON, and any change to the image
    format shows up here first.
    """
    body = json.dumps({str(product_id): f"{size[0]}x{size[1]}"}).encode()
    request = urllib.request.Request(
        f"{target}/rest/image/getProductImages", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    start = time.perf_counter()
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            received = len(response.read())
        ok = True
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        ok = False
    return (time.perf_counter() - start) * 1000.0, ok, received


def run_load(target, duration, concurrency, catalog_size, first_product_id, alpha, size, seed,
             timeout, rate=None):
    """Drive load for `duration` seconds; return (latencies_ms, errors, send_lags_ms, bytes).

    Without `rate` the load is closed-loop: each of `concurrency` threads sends its next
    request as soon as the previous one returns, so throughput is whatever the slowest
    link in the chain allows. With `rate` it is open-loop: request i is due at
    start + i / rate regardless of how fast earlier ones completed, and the threads only
    carry the requests out. Send lag, how late a request left relative to its schedule,
    shows whether the threads kept up; if they did not, the run was not at `rate`.
    """
    start = time.perf_counter()
    deadline = start + duration
    latencies = []
    lags = []
    errors = [0]
    received = [0]
    lock = threading.Lock()
    next_ticket = [0]

    def next_due_time():
        with lock:
            ticket = next_ticket[0]
            next_ticket[0] += 1
        return start + ticket / rate

    def worker(worker_index):
        rng = random.Random(seed + worker_index)
        local = []
        local_lags = []
        local_errors = 0
        local_bytes = 0
        while True:
            if rate:
                due = next_due_time()
                # A driver that fell behind stops at the deadline with its backlog unsent,
                # so an overloaded run cannot stretch the measured window past --duration.
                if due >= deadline or time.perf_counter() >= deadline:
                    break
                wait = due - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                local_lags.append((time.perf_counter() - due) * 1000.0)
            elif time.perf_counter() >= deadline:
                break
            product_id = first_product_id + pick_product(catalog_size, rng, alpha)
            latency, ok, response_bytes = issue_request(target, product_id, size, timeout)
            local.append(latency)
            local_bytes += response_bytes
            if not ok:
                local_errors += 1
        with lock:
            latencies.extend(local)
            lags.extend(local_lags)
            errors[0] += local_errors
            received[0] += local_bytes

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(concurrency)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return latencies, errors[0], lags, received[0]


def load_process(load, warmup, duration, seeds, ready, go, results):
    """One load driver process: warm up, wait for the parent to open the measured
    window, then run the measured phase and hand back what it recorded.

    Worker processes exist because one CPython process cannot drive more than roughly
    a thousand requests per second: its threads share one interpreter lock. The two
    barriers keep the energy window aligned with the load. Every process has finished
    warming up before the parent reads the counters at `ready`, and none starts the
    measured phase before the parent releases `go`.
    """
    run_load(duration=warmup, seed=seeds[0], **load)
    ready.wait()
    go.wait()
    results.put(run_load(duration=duration, seed=seeds[1], **load))


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR.parent / "data"


def git(*arguments):
    # Run in this script's checkout, not the caller's working directory, so the commit
    # recorded is the one the script came from wherever it is started.
    return subprocess.run(["git", *arguments], capture_output=True, text=True, check=True,
                          cwd=SCRIPT_DIR).stdout


def git_commit():
    try:
        return git("rev-parse", "HEAD").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def git_modified_files():
    """Tracked files that differ from the recorded commit, or None if git is unavailable.

    A run made with modified tracked files cannot be reproduced from its commit hash:
    the 28.9. fixed-rate validation run records 0f64467d although measure.py had already
    been changed. Untracked files are ignored, since new run records are untracked
    until committed.
    """
    try:
        lines = git("status", "--porcelain", "--untracked-files=no").splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return [line[3:] for line in lines]


def machine_model():
    """The machine's model from DMI, e.g. "IdeaPad 5 Pro 16ARH7", or None without DMI.

    Recorded instead of the hostname, which on a personal machine often carries a person's
    name into data that is meant to be published. Lenovo puts the model in product_version
    and a type number in product_name; other vendors put the model in product_name.
    """
    dmi = Path("/sys/class/dmi/id")
    try:
        vendor = (dmi / "sys_vendor").read_text().strip()
        field = "product_version" if vendor.upper() == "LENOVO" else "product_name"
        return (dmi / field).read_text().strip() or None
    except OSError:
        return None


def cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def percentile(sorted_values, fraction):
    if not sorted_values:
        return None
    index = min(int(fraction * len(sorted_values)), len(sorted_values) - 1)
    return sorted_values[index]


def measure_idle(args, energy, use_synthetic):
    """Package power over --idle seconds with no load, or None if not requested.

    Most of a lightly loaded laptop's package energy is its idle floor, which is the
    same whatever the service does and dilutes any difference between configurations.
    Recording it lets the run report the energy above idle next to the total.
    """
    if not args.idle:
        return None
    if use_synthetic:
        return SYNTHETIC_IDLE_WATTS
    before = energy.read()
    start = time.perf_counter()
    time.sleep(args.idle)
    after = energy.read()
    joules = headline_joules(energy_delta_joules(energy.max_ranges, before, after))
    return joules / (time.perf_counter() - start) if joules is not None else None


def run_window(args, load, seed, open_window):
    """Warm up, call open_window() as the measured phase starts, run it; return
    (latencies_ms, errors, send_lags_ms, bytes) merged over every thread and process."""
    if args.processes == 1:
        run_load(duration=args.warmup, seed=seed, **load)
        open_window()
        return run_load(duration=args.duration, seed=seed + 500, **load)

    # spawn on every platform, so a run behaves the same on Linux as on the Windows driver.
    context = multiprocessing.get_context("spawn")
    ready = context.Barrier(args.processes + 1)
    go = context.Barrier(args.processes + 1)
    results = context.Queue()
    processes = [
        context.Process(target=load_process, daemon=True,
                        args=(load, args.warmup, args.duration,
                              (seed + 100 * index, seed + 500 + 100 * index), ready, go, results))
        for index in range(args.processes)]
    for process in processes:
        process.start()
    try:
        ready.wait(timeout=args.warmup + 120)
        open_window()
        go.wait(timeout=60)
        parts = [results.get(timeout=args.duration + args.timeout + 60) for _ in processes]
    except (threading.BrokenBarrierError, queue.Empty):
        for process in processes:
            process.terminate()
        raise SystemExit("a load driver process failed or stalled; the run is discarded")
    for process in processes:
        process.join()
    return ([latency for part in parts for latency in part[0]],
            sum(part[1] for part in parts),
            [lag for part in parts for lag in part[2]],
            sum(part[3] for part in parts))


def measure_once(args, repetition, energy, use_synthetic):
    size = tuple(int(part) for part in args.size.split("x"))
    seed = args.seed + repetition * 1000
    load = {"target": args.target, "concurrency": args.concurrency,
            "catalog_size": args.catalog_size, "first_product_id": args.first_product_id,
            "alpha": args.zipf_alpha, "size": size, "timeout": args.timeout,
            "rate": args.rate / args.processes if args.rate else None}

    service = fetch_service_container(args.energy_url, args.target) if args.energy_url else None
    if args.energy_url and service is None:
        print(f"WARNING: no single SUT container publishes the port of {args.target}; "
              "the run will not record which image it measured.")

    idle_watts = measure_idle(args, energy, use_synthetic)

    window = {}

    def open_window():
        window["status"] = fetch_status(args.target)
        window["energy"] = energy.read()
        window["sut_state"] = energy.last_state
        window["start"] = time.perf_counter()

    latencies, errors, lags, received = run_window(args, load, seed, open_window)

    wall_seconds = time.perf_counter() - window["start"]
    energy_after = energy.read()
    sut_state_end = energy.last_state
    status_after = fetch_status(args.target)
    status_before, energy_before = window["status"], window["energy"]

    hits = status_after.get("hits", 0) - status_before.get("hits", 0)
    misses = status_after.get("misses", 0) - status_before.get("misses", 0)
    evictions = status_after.get("evictions", 0) - status_before.get("evictions", 0)

    if use_synthetic:
        joules = {"synthetic-package": (SYNTHETIC_IDLE_WATTS * wall_seconds
                                        + misses * SYNTHETIC_JOULES_PER_RENDER
                                        + hits * SYNTHETIC_JOULES_PER_HIT)}
    else:
        joules = energy_delta_joules(energy.max_ranges, energy_before, energy_after)

    ordered = sorted(latencies)
    ordered_lags = sorted(lags)
    total_energy = headline_joules(joules)
    above_idle = (total_energy - idle_watts * wall_seconds
                  if total_energy is not None and idle_watts is not None else None)
    answered = len(latencies) - errors
    modified = git_modified_files()
    return {
        "label": args.label,
        "repetition": repetition,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mock": use_synthetic,
        "energy_source": "synthetic" if use_synthetic else energy.source,
        "target": args.target,
        "commit": git_commit(),
        # True when tracked files differed from `commit`, so the hash alone does not
        # reproduce the run; the files are listed.
        "commit_dirty": bool(modified) if modified is not None else None,
        "modified_files": modified or [],
        "host": {"model": machine_model(), "system": platform.system(),
                 "machine": platform.machine(), "cpus": os.cpu_count(),
                 "cpu_model": cpu_model()},
        # The machine whose energy was measured; differs from "host" when the load
        # driver is a separate machine reading the SUT through --energy-url.
        "sut_host": energy.sut_host,
        # Power settings, temperatures and battery as read with the energy counters at
        # the start and end of the measured window.
        "sut_state": {"window_start": window["sut_state"], "window_end": sut_state_end},
        # The container that served the load: image id (the build) and JVM options
        # (the cache flag).
        "service_container": service,
        "config": {"duration_s": args.duration, "warmup_s": args.warmup,
                   "concurrency": args.concurrency, "processes": args.processes,
                   "load_model": "open-loop" if args.rate else "closed-loop",
                   "target_rps": args.rate, "idle_s": args.idle,
                   "catalog_size": args.catalog_size,
                   "first_product_id": args.first_product_id,
                   "image_size": args.size, "seed": seed},
        "load_profile": {
            "distribution": "zipf",
            "alpha": args.zipf_alpha,
            "reference": "Breslau et al. 1999, alpha 0.64-0.83 in web proxy traces",
            "top20_share": round(top_share(args.catalog_size, args.zipf_alpha), 4),
        },
        "results": {
            "requests": len(latencies),
            "errors": errors,
            "wall_seconds": round(wall_seconds, 3),
            "throughput_rps": round(len(latencies) / wall_seconds, 2) if wall_seconds else None,
            "response_bytes": {
                "total": received,
                "mean": round(received / answered, 1) if answered else None,
            },
            "network_mbit_s": round(received * 8 / wall_seconds / 1e6, 2) if wall_seconds else None,
            "latency_ms": {
                "median": round(statistics.median(ordered), 3) if ordered else None,
                "p95": round(percentile(ordered, 0.95), 3) if ordered else None,
                "p99": round(percentile(ordered, 0.99), 3) if ordered else None,
            },
            "cache": {"hits": hits, "misses": misses, "evictions": evictions,
                      "entries": status_after.get("entries"),
                      "bytes": status_after.get("bytes"),
                      "max_bytes": status_after.get("max_bytes"),
                      "hit_rate": round(hits / (hits + misses), 4) if (hits + misses) else None},
            "send_lag_ms": {
                "median": round(statistics.median(ordered_lags), 3),
                "p95": round(percentile(ordered_lags, 0.95), 3),
                "max": round(ordered_lags[-1], 3),
            } if ordered_lags else None,
            "energy_joules": {name: round(value, 3) for name, value in joules.items()},
            "package_watts": round(total_energy / wall_seconds, 3)
            if total_energy is not None and wall_seconds else None,
            "idle_package_watts": round(idle_watts, 3) if idle_watts is not None else None,
            "joules_per_request": round(total_energy / len(latencies), 6)
            if total_energy and latencies else None,
            "joules_per_request_above_idle": round(above_idle / len(latencies), 6)
            if above_idle is not None and latencies else None,
        },
    }, ordered


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="configuration name, e.g. cache_on")
    parser.add_argument("--target", default="http://127.0.0.1:8090")
    parser.add_argument("--duration", type=float, default=60.0, help="measured window, seconds")
    parser.add_argument("--warmup", type=float, default=15.0, help="discarded warm-up, seconds")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="threads per load driver process")
    parser.add_argument("--processes", type=int, default=1,
                        help="load driver processes; one CPython process tops out near 1000 req/s")
    parser.add_argument("--rate", type=float,
                        help="fixed total request rate in req/s (open-loop); "
                             "omit for closed-loop load as fast as responses return")
    parser.add_argument("--idle", type=float, default=0.0,
                        help="seconds of idle package power to measure before each run, "
                             "for the energy-above-idle figure; 0 skips it")
    parser.add_argument("--catalog-size", type=int, default=100)
    parser.add_argument("--first-product-id", type=int, default=7,
                        help="id of the most popular product; TeaStore's seeded database starts at 7")
    parser.add_argument("--zipf-alpha", type=float, default=0.8,
                        help="popularity skew; Breslau et al. 1999 measured 0.64-0.83")
    parser.add_argument("--size", default="400x310",
                        help="requested image size, WxH; 400x310 is TeaStore's MAIN_IMAGE preset")
    parser.add_argument("--repeat", type=int, default=1, help="repetitions of this configuration")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--energy", choices=["auto", "rapl", "synthetic"], default="auto")
    parser.add_argument("--energy-url",
                        help="read RAPL from rapl_server.py on the SUT, e.g. http://10.10.10.1:9100, "
                             "instead of from this machine")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--allow-dirty", action="store_true",
                        help="write to data/ even with modified tracked files (recorded in the run)")
    args = parser.parse_args()

    args.target = args.target.rstrip("/")
    if args.processes < 1 or args.concurrency < 1:
        parser.error("--processes and --concurrency must be at least 1")
    if args.rate is not None and args.rate <= 0:
        parser.error("--rate must be positive")
    if args.energy_url:
        # Energy comes from the SUT, not from this machine; a failure here must stop the
        # run rather than fall back to synthetic numbers.
        args.energy_url = args.energy_url.rstrip("/")
        try:
            _, max_ranges, sut_host, _ = fetch_remote_energy(args.energy_url)
        except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError, KeyError) as error:
            parser.error(f"cannot read RAPL counters from {args.energy_url}/energy: {error}")
        energy = SimpleNamespace(max_ranges=max_ranges, source="rapl-powercap-remote",
                                 sut_host=sut_host, last_state=None)

        def read_remote():
            # Keep the state that came with this reading, so the window's start and end
            # state are exactly those of its two energy readings.
            readings, _, _, energy.last_state = fetch_remote_energy(args.energy_url)
            return readings

        energy.read = read_remote
        zones = max_ranges
    else:
        zones = read_rapl_zones()
        energy = SimpleNamespace(read=lambda: read_energy_uj(zones),
                                 max_ranges={name: zone[1] for name, zone in zones.items()},
                                 source="rapl-powercap", sut_host=None, last_state=None)
    if args.energy == "rapl" and not zones:
        parser.error("no readable RAPL zones under /sys/class/powercap "
                     "(try sudo, --energy-url for a remote SUT, or --energy synthetic for a dry run)")
    use_synthetic = args.energy == "synthetic" or (args.energy == "auto" and not zones)
    if args.energy_url and use_synthetic:
        parser.error("--energy-url and --energy synthetic are mutually exclusive")

    size = tuple(int(part) for part in args.size.split("x"))
    missing = check_product_ids(args.target, args.first_product_id, args.catalog_size, size)
    if missing:
        parser.error(f"product ids {missing} are served the 'not found' image; set "
                     "--first-product-id / --catalog-size to a range of real products")

    if not fetch_status(args.target):
        print(f"WARNING: {args.target}/rest/image/state reports no rendered-cache counters. "
              "Is this the thesis build of the image service, and is it up?")

    # Mock runs go to their own directory so they can never be picked up by an
    # aggregate.py summary of real measurements.
    out_dir = Path(args.out) / "mock" if use_synthetic else Path(args.out)

    modified = git_modified_files()
    if (modified and not use_synthetic and not args.allow_dirty
            and out_dir.resolve().is_relative_to(DEFAULT_OUT.resolve())):
        parser.error("tracked files differ from the commit, so results in data/ would not be "
                     f"reproducible from it: {', '.join(modified)}. Commit or stash them, write "
                     "elsewhere with --out, or pass --allow-dirty to record the run anyway.")
    out_dir.mkdir(parents=True, exist_ok=True)

    if use_synthetic:
        print("!" * 78)
        print("! SYNTHETIC ENERGY - numbers below are fabricated to exercise the pipeline.")
        print("! They are NOT measurements and must not appear in the thesis.")
        print("!" * 78)
    else:
        print(f"RAPL zones ({energy.source}): {', '.join(zones)}")

    print(f"load profile: zipf alpha={args.zipf_alpha}, catalog={args.catalog_size}, "
          f"top 20% draws {top_share(args.catalog_size, args.zipf_alpha):.1%} of requests")
    print(f"load model: {f'open-loop at {args.rate:g} req/s' if args.rate else 'closed-loop'}, "
          f"{args.processes} process(es) x {args.concurrency} threads")

    for repetition in range(args.repeat):
        record, latencies = measure_once(args, repetition, energy, use_synthetic)
        stamp = record["timestamp"].replace(":", "").replace("-", "").split(".")[0]
        prefix = f"run_{args.label}_{stamp}_r{repetition}"

        (out_dir / f"{prefix}.json").write_text(json.dumps(record, indent=2))
        with (out_dir / f"{prefix}_latencies.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["request_index", "latency_ms"])
            writer.writerows(enumerate(latencies))

        results = record["results"]
        print(f"[{args.label} r{repetition}] {results['requests']} req  "
              f"{results['throughput_rps']} req/s  "
              f"median {results['latency_ms']['median']} ms  "
              f"hit rate {results['cache']['hit_rate']}  "
              f"misses {results['cache']['misses']}  "
              f"{results['package_watts']} W  "
              f"{results['joules_per_request']} J/req  "
              f"{results['joules_per_request_above_idle']} J/req above idle")

        # An open-loop run that fell behind its schedule was not at the requested rate,
        # so it does not measure what its label claims.
        lag = results["send_lag_ms"]
        if args.rate and lag and (results["throughput_rps"] < 0.98 * args.rate or lag["p95"] > 50):
            print(f"WARNING: requested {args.rate:g} req/s but achieved {results['throughput_rps']} "
                  f"(send lag p95 {lag['p95']} ms). The driver or the link cannot sustain this "
                  "rate; lower --rate or add --processes / --concurrency.")


if __name__ == "__main__":
    main()

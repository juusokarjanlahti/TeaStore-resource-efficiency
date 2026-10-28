# Measuring the rendered-image cache: a reproduction guide

This guide describes how to measure the resource impact of the rendered-image response
cache in the TeaStore image provider, without a physical power meter. It is written so
that someone with different hardware can repeat the experiment and judge whether their
numbers are comparable to ours.

The measurement design is deliberately modest: it establishes whether a specific code
change reduces repeated work, and by how much, on one machine. It is not a calibrated
energy study, and the limitations section says exactly where the line is.

## 1. What is being compared

The same binary is run twice, with the cache enabled and disabled through a runtime
switch. Nothing else changes: same commit, same container image, same host, same load.
That isolates the cache from compilation and environment differences, which is the main
reason the comparison is trustworthy at all.

Evidence is collected at three levels, strongest first:

| Level | Where it runs | What it shows |
|---|---|---|
| 1 | JVM, in-process | hit rate, avoided storage calls, latency — deterministic, no network |
| 2 | JVM, in-process | hit rate as a function of byte budget — the cache sizing curve |
| 3 | Container, over the network | throughput, latency percentiles, energy per request |

Levels 1 and 2 use `RenderedImageCacheBenchmark` in the image module's test sources. It
is deliberately named so Surefire never picks it up, so it cannot destabilise CI. Level 3
uses the scripts in `scripts/` and is what the rest of this guide covers.

## 2. Hardware and topology

Use two machines:

```
  [ load driver ]  --- LAN --->  [ SUT: Docker Compose stack + RAPL sampling ]
```

Running the load generator on the machine being measured is the single biggest source of
error in a setup like this: the generator competes for the same CPU whose energy you are
attributing to the service. If you only have one machine, say so explicitly in your
results and expect the effect size to be understated.

Our SUT was a laptop running Ubuntu. A laptop is acceptable but needs the discipline in
section 4 — thermal behaviour is far less stable than a rack server in a cooled room.

## 3. Software prerequisites

- Docker and Docker Compose on the SUT
- Python 3.8+ on the load driver (standard library only, nothing to install)
- Maven 3.6+ and JDK 11 if you are building the TeaStore images yourself
- `perf` or read access to `/sys/class/powercap` on the SUT for energy

Verify energy access before anything else, because it determines what you can claim:

```bash
ls /sys/class/powercap/
sudo cat /sys/class/powercap/intel-rapl:0/energy_uj
perf list | grep -i energy
```

RAPL is available on Intel since Sandy Bridge and on AMD since Zen. If nothing appears,
you are on hardware or in a VM that does not expose it; the harness will fall back to
synthetic values that are clearly marked and unusable as results. In that case report
counter-level evidence (instructions, cache misses per request) instead of energy.

Note that read permission on `energy_uj` is commonly root-only, a mitigation for the
PLATYPUS side-channel. Running the driver under `sudo` is expected.

## 4. Preparing the environment

Each of these removes a specific confound. Skipping them does not make the measurement
impossible, but it does mean unexplained variance you will have to account for.

```bash
sudo cpupower frequency-set -g performance      # stop the governor changing clocks mid-run
echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo   # optional; document either way
```

- Run the SUT on AC power. On battery, the platform will trade clocks for endurance.
- Close everything else. Browsers and sync clients produce load that lands in package energy.
- Prefer a console session over a desktop session; a compositor is not free.
- Let the machine reach a steady thermal state before the first measured run.

## 5. Running

### Step 0 — dry run against the stub

Validate the pipeline before involving the real stack. `stub_image_service.py` speaks the
same REST contract as the real service, so this exercises the actual request and parsing
code:

```bash
python scripts/stub_image_service.py --cache on --cache-size 300000 --port 8090
python scripts/measure.py --label dryrun --target http://127.0.0.1:8090 \
    --duration 12 --warmup 3 --energy synthetic
```

Energy from a dry run is fabricated. The harness prints a warning, tags each record with
`"mock": true`, and writes to `data/mock/` so these records can never be picked up by a
summary of real runs.

### Step 1 — start the stack and confirm it is ready

```bash
docker compose -f thesis/measurements/docker-compose.measure.yaml up -d
curl http://<sut>:8081/tools.descartes.teastore.image/rest/image/finished   # cache on
curl http://<sut>:8082/tools.descartes.teastore.image/rest/image/state      # cache off
```

The measurement Compose file runs only what the image service needs, and runs it three
times from the same image: port 8081 with the cache enabled, port 8082 with it disabled
(`-Dimage.renderedCache.enabled=false`), and port 8083 with it disabled and product images
in JPEG (`-Dimage.productFormat=jpeg`) for the separate image format comparison. The flag is read from a JVM system property that
has no REST endpoint, so running both side by side is what makes interleaving possible
without restarts. See [SETUP.md](SETUP.md) for the full two-machine setup and the
validation script.

The `state` endpoint is text/plain and includes the rendered-cache counters the harness
parses. Image generation on first start takes a while; do not measure until `finished`
returns true, or the baseline will include generation work.

### Step 2 — interleaved A/B runs

Alternate configurations rather than running all of one and then all of the other. On a
laptop that warms up over a session, sequential ordering lets thermal drift masquerade as
an effect. `aggregate.py` checks timestamps and warns if it sees a sequential pattern.

```bash
SUT=http://<sut>
for i in 1 2 3 4 5; do
  python scripts/measure.py --label cache_off --target $SUT:8082/tools.descartes.teastore.image \
      --energy-url $SUT:9100 --duration 120 --warmup 30
  python scripts/measure.py --label cache_on  --target $SUT:8081/tools.descartes.teastore.image \
      --energy-url $SUT:9100 --duration 120 --warmup 30
done
```

`scripts/run_ab.sh` (and `run_ab.ps1` for a Windows driver) does the same and also
alternates which configuration goes first, ten pairs by default; the grounds for that
number are in KUVIOT_JA_TAULUKOT.md, section 3. Report median and spread, never a single
run.

The loop above is closed-loop and, on a gigabit link, ends up measuring the link rather
than the service (section 6). For the comparison itself, run at a fixed rate below the
ceiling with idle power recorded:

```bash
./scripts/run_ab.sh --sut <sut> --rate 800 --processes 3 --concurrency 8 --idle 20
```

### Step 3 — cache size sweep

> **Not wired up yet.** `setCacheSize` below resizes the storage-level `IDataCache`, not
> the rendered-image cache: `ImageProvider.setRenderedCacheMaxSize` exists but no REST
> endpoint calls it. The sweep needs that endpoint (or a startup property for the budget)
> before it measures what this section describes.

The byte budget can be changed at runtime, so the sweep needs no restarts:

```bash
curl -X POST -H 'Content-Type: application/json' -d '5000000' \
    $TARGET/rest/image/setCacheSize
```

Choose sweep points as fractions of the rendered working set, not as round numbers.
Measure the working set first: run one pass over the catalog with a large budget and read
`Rendered Image Cache Size (Bytes)` from `state`. Then sweep at roughly 5, 10, 25, 50 and
100 percent of it.

This matters more than it sounds. If every budget in your sweep exceeds the working set,
nothing is ever evicted, hit rate pins near 1.0 and the curve is flat — you will have
measured nothing. Breslau et al. (1999) predict hit rate rising logarithmically with cache
size, and you can only observe that if the smaller budgets genuinely bind.

### Step 4 — summarise

```bash
python scripts/aggregate.py --out summary.csv
```

## 6. The load profile

Requests are drawn from a Zipf-like popularity distribution, implemented in
`pick_product()`. This is not arbitrary: Breslau et al. (1999) analysed six independent
web proxy traces and found request popularity follows Zipf-like behaviour with alpha
between roughly 0.64 and 0.83, and derived from it the logarithmic relationship between
cache size and hit rate. The default here is alpha = 0.8, and each run records the alpha
and the resulting share of requests drawn by the most popular fifth of the catalog.

A uniform profile would be the wrong choice, and not only because it is less realistic:
it spreads requests so thinly that almost everything misses, which hides the exact effect
the cache exists to produce. The opposite error is equally real — a very high alpha
concentrates requests onto a handful of products and flatters the cache.

Because alpha is a single parameter, robustness is cheap to demonstrate. Repeat the
headline comparison at a less favourable alpha, for example 0.6, and report both. A result
that survives its own worst reasonable case is much harder to argue with.

### Why not TeaStore's bundled load generators

`examples/` ships three (HTTP Load Generator, JMeter, Locust). All of them drive the
WebUI as shoppers, and all of them pick products uniformly: Locust with
`randint(7, 506)`, the HTTP Load Generator's `teastore_browse.lua` with `math.random`
over the links on a page. Uniform selection over 500 products is close to the worst case
for any cache and does not match observed web popularity, so these profiles would
systematically understate a caching effect. They also load every service in the stack,
which dilutes a change confined to the image provider. They remain the right tool for a
whole-application realism check, which is a different question.

### Load model and choosing the rate

`measure.py` has two load models:

- **Closed-loop** (default): each thread sends its next request as soon as the previous
  one returns. Throughput is whatever the slowest link allows.
- **Open-loop at a fixed rate** (`--rate`): request *i* is due at *start + i/rate*
  regardless of how earlier ones fared. Both configurations then serve the same requests
  at the same rate, so a difference in power comes from the code. This is the principle
  behind SPECpower's fixed load levels, and the HTTP Load Generator's intensity profiles
  work the same way.

Compare at a fixed rate. On our bench, closed-loop load hit two ceilings before the SUT's
CPU did: one CPython driver process tops out near 1,000 req/s because its threads share
the interpreter lock, and at 400x310 the Zipf-weighted response is 76 KiB of base64 PNG,
so a gigabit link saturates near 1,490 req/s. The SUT's package power rose only about
3.6 W above idle. In that regime both configurations are pinned to the same throughput by
construction, which says nothing about the change.

To choose the rate, probe first: closed-loop runs at rising `--concurrency` (and
`--processes`) until throughput stops rising, then pick a rate comfortably below that
ceiling. Each open-loop run records its send lag; if the p95 exceeds 50 ms or the achieved
rate falls short by more than 2 %, `measure.py` warns that the driver could not keep up and
the run was not at the requested rate. `--processes` is needed above roughly 1,000 req/s.

Record idle power with `--idle` (seconds measured before each run). A lightly loaded
laptop spends most of its package energy on its idle floor, 80 % in our 600 req/s trial,
and that floor varied between 2.5 and 4.2 W within one day. The record then carries both
`joules_per_request` and `joules_per_request_above_idle`; report both.

## 7. Reading the output

Each run writes two files:

- `run_<label>_<timestamp>_r<n>.json` — configuration, host, commit, load profile, summary
- `run_<label>_<timestamp>_r<n>_latencies.csv` — every request latency, for distribution plots

Every record carries the git commit and the load profile, so a result can always be traced
back to the code and the workload that produced it. Each record also carries the evidence
that the conditions were what the comparison assumes:

- **commit_dirty** and **modified_files**: whether tracked files differed from `commit`.
  `measure.py` refuses to write such runs to `data/` without `--allow-dirty`.
- **service_container**: the image id of the container that served the load, identical
  for both configurations if they ran one build, and its JVM options, which carry the
  cache flag.
- **sut_state.window_start** and **window_end**: turbo, governor, energy preference, AC,
  temperatures and battery, read together with the energy counters at both ends of the
  measured window.
- **response_bytes** and **network_mbit_s**: response size and link usage, the mechanism
  behind any change to the image format.

`aggregate.py` compares these across runs and warns when a condition that must be shared
differs, such as the build, turbo, rate or commit, or when a run was on battery.

The headline metric is **joules per request**, not watts. Watts describe how hard the
machine was working, which varies with how long the run happened to take; energy per unit
of work is what actually compares two implementations, and it matches how the Green
Software Foundation's SCI specification expresses intensity against a functional unit.

Read alongside it:

- **hit rate** — the mechanism. If this did not move, nothing else should be believed.
- **misses** — renders and storage loads actually avoided.
- **evictions** — confirms the budget was binding. Zero evictions means the sweep point was too generous.
- **latency percentiles** — the user-visible effect; medians alone hide tail behaviour.
- **package_watts** and **idle_package_watts** — how far above idle the run actually
  pushed the SUT. A run barely above idle is not loading the service.
- **joules_per_request_above_idle** — the part of the energy that depends on the work done.
- **send_lag_ms** (open-loop only) — whether the driver kept to schedule.

## 8. Limitations

State these; they raise the credibility of the result rather than lowering it.

- **Energy is package-scope, not system-scope.** RAPL covers the CPU package and, on many
  parts, integrated graphics. Disk, network and the rest of the platform are excluded. The
  reported figure is not wall power and should never be presented as such.
- **RAPL is a vendor-provided estimate**, not an external instrument. Schmitt et al. (2019)
  declined it for this reason when building a portable power model. For a paired A/B on
  identical hardware, where both sides share any systematic bias, it is defensible — but
  the absolute magnitude carries more uncertainty than the difference does.
- **One machine, one configuration.** Results do not generalise across hardware. The
  direction of the effect is likely to; the magnitude is not.
- **No function-level attribution.** Assigning energy to individual functions, as Schmitt
  et al. do, needs per-host regression coefficients trained against a power meter. Their
  own portability analysis reports 10–21% mean absolute error when moving a trained model
  between three servers of the same CPU family, so borrowing published coefficients for
  different hardware would produce precise-looking numbers with no validity.
- **A laptop SUT is thermally noisy.** Interleaving and repetition mitigate this; they do
  not eliminate it. Measure on AC power with a full battery: on battery, power management
  differs and can change as charge falls.
- **Network energy is not measured.** RAPL sees the SUT's package only. For changes that
  reduce bytes on the wire, such as image format, the reported saving is a lower bound.
- **One service, not the application.** Load goes straight to the image provider. The
  result does not say how much the whole store saves under real users.

## 9. Adapting this to another change

The harness is not specific to the image cache. To evaluate a different change, you need:
a runtime switch so both sides share one binary; a counter the service already exposes so
the mechanism is observable and not merely inferred; a load profile you can justify from
literature rather than convenience; and a metric expressed per unit of work rather than
per unit of time.

If any of those four is missing, the measurement will be difficult to defend regardless of
how carefully the numbers are collected.

## References

- Breslau, L., Cao, P., Fan, L., Phillips, G. & Shenker, S. 1999. Web Caching and Zipf-like
  Distributions: Evidence and Implications. IEEE INFOCOM '99.
- Schmitt, N., Iffländer, L., Bauer, A. & Kounev, S. 2019. Online Power Consumption
  Estimation for Functions in Cloud Applications. IEEE ICAC 2019.
- von Kistowski, J., Eismann, S., Schmitt, N., Bauer, A., Grohmann, J. & Kounev, S. 2018.
  TeaStore: A Micro-Service Reference Application for Benchmarking, Modeling and Resource
  Management Research. IEEE MASCOTS 2018.

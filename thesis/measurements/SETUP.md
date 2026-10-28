# Two-machine setup: SUT laptop and load-driver laptop

This is the practical companion to [MEASUREMENT_GUIDE.md](MEASUREMENT_GUIDE.md): the steps
to get from two bare machines to a validated setup, and how to tell which link in the
chain is broken when something does not work. The *why* behind the design is in the
measurement guide; this file is the *how*.

```
  Ubuntu laptop (load driver)             Ubuntu laptop (SUT, has RAPL)
  10.10.10.2/24                           10.10.10.1/24
  ┌─────────────────────┐    ethernet     ┌──────────────────────────────────────┐
  │ measure.py          │ ──────────────> │ :8081  image service, cache ON       │
  │ run_ab.sh           │                 │ :8082  image service, baseline       │
  │ run_sweep.sh        │                 │ :8083  image service, JPEG           │
  │ check_setup.sh      │ ──────────────> │ :9100  rapl_server.py (energy)       │
  └─────────────────────┘                 │        registry, persistence, db     │
                                          └──────────────────────────────────────┘
```

The load driver runs on a machine of its own so that generating requests does not use the
CPU whose energy is being measured. In this work it is a second Ubuntu laptop running the
bash scripts (TESTIPENKKI.md, section 1); a Windows machine with the PowerShell versions
works too (B6). The SUT's RAPL counters are read over the link through `rapl_server.py`.
The direct cable has no gateway, so the driver keeps its internet connection over Wi-Fi.
The SUT needs none once the stack is built, and D1 turns its Wi-Fi off.

All configurations run at the same time, from the same Docker image, and differ only in
their JVM options: 8082 is the baseline (PNG, rendered cache off), 8081 turns the cache
on and 8083 switches product images to JPEG with the cache off. The cache and the image
format are two separate comparisons against the one baseline. The containers not under
load idle, and that idle cost is present in every configuration.

## Part A: the SUT laptop

### A1. Hardware

- **RAM.** The stack needs roughly 3–3.5 GB on top of the operating system, with three
  image service JVMs. On a 4 GB
  laptop that means swapping, which puts disk I/O and extra CPU work into the energy
  figures. Upgrade before measuring. Whatever the final hardware is, it must be the same
  for every run you compare.
- **Power.** Always measure on AC power. On battery, the platform lowers clocks to save
  energy.

### A2. Software

```bash
sudo apt install docker.io docker-buildx docker-compose-v2 openjdk-11-jdk maven linux-tools-common
sudo usermod -aG docker $USER      # log out and back in for this to take effect
java -version                      # must say 11; if not: sudo update-alternatives --config java
docker run --rm hello-world        # confirms Docker works without sudo
```

### A3. Network

A NetworkManager profile `teastore-lan` gives the ethernet port a static address. It is
separate from the default "Wired connection 1", which it leaves untouched:

```bash
nmcli con add type ethernet ifname enp1s0 con-name teastore-lan \
    ipv4.method manual ipv4.addresses 10.10.10.1/24 ipv4.never-default yes \
    ipv6.method disabled connection.autoconnect-priority 10
nmcli con up teastore-lan
```

Check the link once the driver is configured (B1):

```bash
ip -br addr show enp1s0            # UP, 10.10.10.1/24
cat /sys/class/net/enp1s0/speed    # 1000 on gigabit; -1 means no cable or no carrier
```

If `ufw` is active, allow the energy server on the link only. Docker's published ports
(8081, 8082, 8083) bypass ufw and need no rule.

```bash
sudo ufw status
sudo ufw allow in on enp1s0 to any port 9100 proto tcp
```

### A4. Build

From the repository root, on this branch:

```bash
git switch thesis/rendered-image-cache
mvn clean install -DskipTests
(cd tools && ./build_docker.sh)    # the "DEPLOY: unary operator expected" line is harmless
```

`build_docker.sh` tags the local images with the same names as the upstream images on
Docker Hub. The Compose file refuses to pull, so if a local build is missing it fails
instead of quietly running an upstream image that has no cache.

### A5. Start the stack

```bash
docker compose -f thesis/measurements/docker-compose.measure.yaml up -d
docker compose -f thesis/measurements/docker-compose.measure.yaml logs -f image-cache-on
```

Each image service generates its images at startup, which takes several minutes. Wait
until all three report `true`:

```bash
for port in 8081 8082 8083; do
  curl -s localhost:$port/tools.descartes.teastore.image/rest/image/finished; echo " ($port)"
done
```

Until generation is done, the endpoint answers with HTTP 500 and `false`. That is normal.

### A6. Start the energy server

Reading `energy_uj` requires root. The server binds only to the cable's address, so the
counters are not exposed on Wi-Fi. The link must be up first, or binding fails with
"Cannot assign requested address".

```bash
sudo systemd-inhibit --what=sleep:idle:handle-lid-switch --why="TeaStore measurement" \
    python3 thesis/measurements/scripts/rapl_server.py --bind 10.10.10.1
```

Leave it running in its own terminal for the whole session. `systemd-inhibit` keeps the
SUT from suspending, and from reacting to the lid, for as long as the server runs.
Without it, closing the lid suspends the SUT: the default lid action is suspend, and
the desktop's "never sleep" setting no longer applies once the desktop is stopped (D1).

### A7. SUT-side checks

```bash
docker compose -f thesis/measurements/docker-compose.measure.yaml ps   # 6 services, all Up
curl -s localhost:8081/tools.descartes.teastore.image/rest/image/state | grep Rendered
curl -s http://10.10.10.1:9100/energy                                   # zones incl. package-0
free -h                                                                 # Swap "used" should be ~0
```

The `state` output must contain `Rendered Image Cache ...` lines. If it does not, the
container runs an image without the cache, and the build in A4 did not produce the images
Compose is using.

## Part B: the load driver

The driver in this work is an Ubuntu 24.04 laptop (TESTIPENKKI.md, section 1). It runs the
bash scripts in `scripts/`, which need only `python3`, `curl` and `ssh`. A Windows machine
works as well with the PowerShell versions (B6).

### B1. Static IP on the ethernet adapter

A NetworkManager profile like the SUT's (A3). Find the adapter with `ip -br link`; a USB
adapter's name starts with `enx`.

```bash
nmcli con add type ethernet ifname <adapter> con-name teastore-lan \
    ipv4.method manual ipv4.addresses 10.10.10.2/24 ipv4.never-default yes \
    ipv6.method disabled connection.autoconnect-priority 10
nmcli con up teastore-lan
ping -c2 10.10.10.1
cat /sys/class/net/<adapter>/speed     # 1000 on gigabit
lsusb -t | grep r8152                  # a USB adapter: 5000M or 10000M, not 480M
```

A USB adapter also has to run at USB 3 speed. At 480M, which is USB 2.0, the link still
reports 1000 Mb/s but carries only about 330 Mbit/s, and runs at 1000 req/s fall seconds
behind their schedule. This happened on 6.10.2026 after the adapter had been plugged into a
USB-C port without its USB 3 lanes connecting; reseating it fixed it. `r8152` is the driver of
Realtek's USB adapters; for another adapter, look for its driver in `lsusb -t`.

Leaving out the gateway matters (`ipv4.never-default yes`). With a gateway, the driver may
route its internet traffic through the SUT, which adds network load on the machine being
measured. A direct cable needs no crossover cable; current adapters detect the direction
automatically.

### B2. Software

Ubuntu 24.04 comes with `python3` (3.12) and `curl`. Install the rest if missing:

```bash
sudo apt install git openssh-client curl
python3 --version        # 3.8 or newer
```

The measurement scripts use only the Python standard library, so there is nothing to install
with pip. `image_quality.py` is the exception and runs in a container (README.md).

### B3. The code

The driver needs the same commit as the SUT, because every result records the commit it
was produced with. Clone the repository on both machines:

```bash
git clone <repository URL> TeaStore-resurssitehokkuus
cd TeaStore-resurssitehokkuus
git switch thesis/rendered-image-cache
git log -1 --oneline        # the same on both machines
```

Before every session, `git pull` on both and compare `git log -1 --oneline`. `measure.py`
refuses to write into `data/` from a checkout with modified tracked files, so commit
changes before a session, not during it.

### B4. An ssh key for the SUT

`run_ab.sh --fresh-ssh` (D6) and `run_sweep.sh --ssh` (D5) recreate containers on the SUT
over ssh, many times in a session, so the login must not ask for a password:

```bash
sudo apt install openssh-server        # on the SUT, once
ssh-keygen -t ed25519                  # on the driver; skip if ~/.ssh/id_ed25519 exists
ssh-copy-id 10.10.10.1
ssh -o BatchMode=yes 10.10.10.1 true && echo ok
```

Without a user part, ssh logs in with the driver's user name. If the names differ between
the machines, write `<user>@10.10.10.1` here and in D5 and D6.

### B5. Keep the driver awake

A series takes hours, and if the driver suspends partway through, the series fails. Check
once that *Settings → Power → Automatic Suspend* is off when plugged in. Closing the lid
still suspends, so long series run under `systemd-inhibit`, which also blocks the lid (D6). Close anything heavy on the driver: a busy driver keeps to
the rate less well, which `measure.py` reports as send lag.

### B6. Windows driver instead

`check_setup.ps1`, `run_ab.ps1` and `run_sweep.ps1` do the same as the bash scripts, with
options in `-PascalCase` (`--rate 1000` becomes `-Rate 1000`), but they lack `--fresh-ssh`
and `--skip-pairs`, so the fresh-JVM series (D6) needs the bash script. The validation on
28.9.2026 was run this way (TESTIPENKKI.md, section 2).

- **Static IP.** *Settings → Network & internet → Ethernet → IP assignment → Edit →
  Manual*, IPv4 on, IP address `10.10.10.2`, prefix length `24`, gateway and DNS empty. Or
  in an administrator PowerShell, `New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress
  10.10.10.2 -PrefixLength 24`, with the alias from `Get-NetAdapter`. A ping from the SUT
  to the driver usually fails, because the Windows firewall blocks inbound ping; all
  connections go from the driver to the SUT, so that does not matter.
- **Python and Git.** `winget install Python.Python.3.12` and `winget install Git.Git`, then
  a new terminal. If `python` opens the Microsoft Store, turn off the `python.exe` and
  `python3.exe` entries under *Settings → Apps → Advanced app settings → App execution
  aliases*.
- **Running .ps1 scripts.** Once, `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or
  run each script with `powershell -ExecutionPolicy Bypass -File <script>.ps1`.
- **Sleep.** *Settings → System → Power*: sleep *Never* while plugged in.
- **Part D** then reads `python` for `python3`, backslashes in paths, and the `.ps1`
  scripts with their options.

## Part C: validate

With the SUT at A7 and the driver at B5, run the check script from the repository root on
the driver (on Windows, `check_setup.ps1 -Sut 10.10.10.1`):

```bash
./thesis/measurements/scripts/check_setup.sh --sut 10.10.10.1
```

It stops at the first failure. Each stage relies on the ones before it:

| Stage | What passing shows | Usual cause of failure |
|---|---|---|
| 1. Python | 3.8+ on PATH | `python3` missing (B2); on Windows, the app execution alias (B6) |
| 2. Network | ports 9100, 8081, 8082 reachable | Link down (A3/B1), energy server not started (A6), stack not up (A5), ufw |
| 3. RAPL | energy counters readable and increasing; prints idle package power | `rapl_server.py` not run with sudo |
| 4. Image services | image generation finished, thesis build running | Still generating (wait); Docker Hub image instead of the local build (A4) |
| 5. Catalogue | the requested product ids are real products, not the "not found" placeholder | Database was regenerated with different ids; set `--first-product-id` |
| 6. Cache flag | port 8081 counts hits and port 8082 counts nothing | Flag not passed (`CATALINA_OPTS` in the Compose file) |
| 7. Smoke run | full `measure.py` path works end to end; prints power under load | Look at the error `measure.py` prints |

What healthy numbers look like on an i5-8250U, as a rough guide:

- **Idle package power** (stage 3) of a few watts. Above about 8 W, something else is
  running on the SUT: the desktop, a browser, VS Code, or image generation that has
  not finished.
- **Power under load** (stage 7) clearly above idle, typically 10–25 W. If it is barely
  above idle, the load is not reaching the SUT's CPU. The driver or the link may be
  the bottleneck.
- **Hit rate** for `smoke_cache_on` well above zero. For `smoke_cache_off` there is no hit
  rate at all.

The smoke runs go to `/tmp/teastore-smoke` (`%TEMP%\teastore-smoke` on Windows), never to
`data/`. Their numbers are not results.

## Part D: measuring

Every session follows the same order: conditions (D1), noise check (D3), series (D4).
The rate (D2) is chosen once, in the first session, and then kept for every series.

### D1. Conditions on the SUT

1. AC power, lid open (some laptops throttle with the lid closed).
2. **Battery not charging.** Turn on the IdeaPad charge limit ("conservation mode") once.
   The battery then stays at about 60 % and the SUT runs from the mains:
   ```bash
   ls /sys/bus/platform/drivers/ideapad_acpi/                  # find the device, e.g. VPC2004:00
   echo 1 | sudo tee /sys/bus/platform/drivers/ideapad_acpi/VPC2004:00/conservation_mode
   cat /sys/class/power_supply/BAT*/status                     # wait for "Not charging"
   ```
   Charging does not enter package energy directly, but it warms the chassis, and the
   firmware may manage power differently while it charges. `aggregate.py` warns about any
   run with a charging or discharging battery.
3. Close VS Code, browsers and sync clients. Then stop the desktop: log into a text
   console (Ctrl+Alt+F3) and run `sudo systemctl stop gdm`. Logging out is not enough,
   because the login screen runs a `gnome-shell` of its own. Start the energy server
   (A6) in that console, and use a second console (Ctrl+Alt+F4) for the rest.
4. Turn off Wi-Fi: `nmcli radio wifi off`. The stack does not need internet once it is
   built.
5. Fix the CPU frequency policy: `sudo cpupower frequency-set -g performance`, and keep
   turbo off with the systemd unit in [system/](system/teastore-no-turbo.service). The unit
   sets it at every boot and after every resume; its header has the install commands.
   Turbo matters because on a laptop the turbo clock follows temperature, and because the
   firmware may pick a different setting depending on whether the SUT booted on AC.
   Ubuntu's power menu then reports performance mode as "degraded due to high operating
   temperature". That label only means turbo is off, not that the SUT is hot.
   `rapl_server.py` records turbo, governor, energy preference, AC state, temperatures
   and battery in every run record under `sut_state`, and `aggregate.py` warns when these
   differ between runs.
6. Once the stack has finished starting and `rapl_server.py` runs, prepare the session:
   ```bash
   sudo thesis/measurements/scripts/sut_session.sh --quiet
   ```
   It checks steps 1–5 and that sleep is blocked (A6), stops background services that
   wake the CPU on their own schedule (updates, snap, firmware checks, printing,
   Bluetooth) until the next reboot, and saves the running processes, services and the busiest processes over 10 s of idle.
   It then waits at least 10 minutes until the package temperature has settled
   (measurement plan, section 5.1) and prints the session's idle package power. Output:
   `validation/<date>/session_<time>/`, in UTC like the run records. If it reports
   `WARN`, fix that first. If the idle power is clearly above that of earlier sessions,
   compare `idle_top.txt` and `processes.txt` between the sessions to find what is
   running.
7. Write the session line in `data/SESSIOLOKI.md`: the idle power from step 6 and the room
   temperature at the start and at the end. Read the temperature from a thermometer beside
   the SUT, not on it, and keep the SUT in the same spot on a hard surface every
   session.

### D2. Choosing the rate (first session only)

The comparison runs at a fixed rate, and at that rate nothing may saturate: not the
driver, not the link, not the SUT. A saturated component queues requests, and then the
two configurations no longer do the same work. Within that limit, the higher the rate the
better: idle power is spread over more requests, and the part of the energy that the code
affects grows. The reasoning is in [TESTIPENKKI.md](TESTIPENKKI.md), section 6.

Step the rate up with the cache off:

```bash
cd thesis/measurements/scripts
for r in 400 600 800 1000 1200; do
    python3 measure.py --label ladder_$r --target http://10.10.10.1:8082/tools.descartes.teastore.image \
        --energy-url http://10.10.10.1:9100 --energy rapl --rate $r --processes 3 --concurrency 8 \
        --duration 60 --warmup 20 --idle 20 --out ../validation/<date>/ladder
done
```

`<date>` is the UTC date (`date -u +%F`), the same as in the session folder that
`sut_session.sh` writes on the SUT.

Choose the highest step that meets all of these:

- `measure.py` did not warn that the driver fell behind (achieved rate within 2 %, send
  lag p95 below 50 ms).
- `network_mbit_s` is at most 660, i.e. 70 % of the roughly 940 Mbit/s a gigabit link
  carries. Queueing delay grows steeply as utilisation approaches 100 %.
- Latency p95 is at most 1.5 times that of the 400 req/s step. A steeper rise is the knee
  where something has started to queue.

At 400x310 the Zipf-weighted response is 76 KiB, so 1,000 req/s is about 625 Mbit/s: expect
1,000 req/s to be the answer. The ladder also gives figure K1 (power against rate).

### D3. Noise check (every session, before the series)

Run the cache-off configuration five times at the chosen rate, with the same parameters
as the series, and summarise:

```bash
cd thesis/measurements/scripts
python3 measure.py --label noise --target http://10.10.10.1:8082/tools.descartes.teastore.image \
    --energy-url http://10.10.10.1:9100 --energy rapl --duration 120 --warmup 30 --repeat 5 \
    --rate <rate> --processes 3 --concurrency 8 --idle 20 \
    --out ../validation/<date>/noise
python3 aggregate.py --data ../validation/<date>/noise
```

The energy CV (stdev / mean of J/req) decides the number of pairs, before any A/B data
exists: at most 15 % means 10 pairs. Above 15 %, fix the conditions in D1 and repeat
the check; if it stays above 15 %, run 15 pairs (`run_ab.sh --repeat 15`) and report the
spread as a limitation. The reasoning is in KUVIOT_JA_TAULUKOT.md, section 3. The fresh-JVM
series (D6) has 20 pairs whatever the CV. Its pairs differ by more than run-to-run noise, and
the noise check measures only the latter, but it still has to pass before that series starts.

The records stay in `validation/`: noise checks from different days are the evidence for
the measurement plan's repeatability criterion (section 5.4). If the CV is too high, the
records usually show why: idle power that varies between runs points to a background
process, a temperature that keeps rising to a SUT that had not settled, and a battery
status of "Charging" to step 2 of D1.

### D4. The series

```bash
cd thesis/measurements/scripts
# the cache: cache_off (8082) vs cache_on (8081), to data/
./run_ab.sh --rate <rate> --processes 3 --concurrency 8 --idle 20
# robustness at a less favourable skew, kept apart from the main series
./run_ab.sh --rate <rate> --processes 3 --concurrency 8 --idle 20 --alpha 0.6 --out ../data/alpha06
# the image format: png (8082) vs jpeg (8083), cache off in both, to data/format/
./run_ab.sh --compare format --rate <rate> --processes 3 --concurrency 8 --idle 20
```

`measure.py` warns after any run that did not keep to the requested rate; discard those.
Before the first run, `run_ab.sh` checks that every container serves product images in
the format its label claims and, for the cache series, that the budget is 3 MiB.

Each series runs ten pairs, alternating which configuration goes first, with a cooldown
before every run, and ends with `aggregate.py` over its own directory. Ten pairs take
about 80 minutes, so two series fit in one session. Every series goes to a directory of
its own, because `aggregate.py` treats everything in one directory as one comparison and
warns when, for example, alpha differs. Results are written under
`thesis/measurements/data/` on the driver; commit them from there. These three series were
measured in session 1 and 2. `run_ab.sh` refuses to measure a seed that its output
directory already holds, so a repeat of one needs a directory of its own.

### D5. Cache budget sweep (one session, figure K5)

The sweep measures the cache-on container at 1, 2, 3, 4, 8 and 16 MiB and the cache-off
container as the same-session reference, 3 runs each, in a fixed random order. The budget
is a JVM option, `-Dimage.renderedCache.maxBytes`, set through `RENDERED_CACHE_BYTES` in
the Compose file, so the container is recreated for every budget. This needs the image
built from this branch after the option was added (A4).

Do D1 and D3 first, then on the driver, at the rate of the main series:

```bash
./thesis/measurements/scripts/run_sweep.sh --rate <rate> --ssh 10.10.10.1
```

With `--ssh` the script recreates the container itself, which needs the ssh key from B4.
Without `--ssh` it prints the command to run on the SUT and waits for Enter.
Before measuring each point it checks from the state endpoint that the budget in effect is
the intended one, and at the end it puts the container back to 3 MiB. `run_ab.sh` refuses
to start if the cache-on container reports any other budget than 3 MiB.

Results go to `data/sweep/`, apart from the main series, and take about 1.5–2 hours.
`aggregate.py` warns that the configurations look sequential; that is expected here, since
the runs of one budget are kept together and the order of budgets is randomised instead.

Leave the first run of every budget out of the analysis. It comes right after the container
was recreated, and in the 1.10.2026 sweep it had higher idle power (1.6–1.8 W instead of about
1.47 W) and about 1 % higher J/req. Each budget is also a single JVM instance, so read the curve
as indicative: two instances of one configuration can differ by 1.5–3.5 % (TESTIPENKKI.md,
section 5).

### D6. Cache series on fresh JVMs (session 3)

The session 1 cache series compared one cache-on JVM with one cache-off JVM. Two instances of
the same configuration can differ by about as much as the cache effect (TESTIPENKKI.md,
section 5), so the series is repeated with new instances for every pair. Before each pair,
`run_ab.sh` recreates both containers over ssh, waits for image generation, checks the format
and the 3 MiB budget, and runs one settling run per container into `<out>/settle/`.
`aggregate.py` does not read that folder. Then it runs the measured pair as usual.

The series has **20 pairs**, decided on 6.10.2026 before any of its data existed. A fresh pair's
difference includes the difference between two instances, and with 10 pairs the test would
have found a 1.6 % saving only about every other time (KUVIOT_JA_TAULUKOT.md, section 3;
`scripts/pair_power.py`). A pair takes about 16 minutes (recreate and generation about 40 s,
then four runs of about 4 minutes), so the series takes about 5 h 25 min, and the whole
session with D1 and D3 about 6 h. Start in the morning.

1. **D1, steps 1–5.** The energy server runs from step 3 on.
2. **Smoke test, once before the first fresh series.** `--fresh-ssh` had not yet run end to
   end before session 3. It comes before `sut_session.sh`, so that the settling there covers
   the JVMs it recreates:
   ```bash
   cd thesis/measurements/scripts
   ./run_ab.sh --rate 1000 --processes 3 --concurrency 8 --idle 5 --repeat 2 --duration 10 \
       --warmup 5 --cooldown 5 --fresh-ssh 10.10.10.1 --out /tmp/fresh-smoke
   ```
   About 6 minutes. It passes if both pairs run and the paired summary at the end reports
   `cache_off: 2 JVM instance(s) over 2 runs` and the same for `cache_on`. Its numbers are not
   results.
3. **D1, steps 6–7, and D3** as in every session.
4. **The series**, on the driver. `systemd-inhibit` keeps the driver from suspending, also with
   its lid closed, and `tee` keeps `measure.py`'s warnings, which would otherwise scroll out of
   the terminal over five hours, in the session's validation folder:
   ```bash
   cd thesis/measurements/scripts
   systemd-inhibit --what=sleep:handle-lid-switch --why="TeaStore series" \
       ./run_ab.sh --rate 1000 --processes 3 --concurrency 8 --idle 20 --repeat 20 \
       --fresh-ssh 10.10.10.1 --out ../data/fresh \
       2>&1 | tee -a ../validation/$(date -u +%F)/run_ab_fresh.log
   ```

This needs the ssh key from B4. Each pair's two runs record the container `id` and
`started_at` under `service_container`, and `aggregate.py` prints how many instances each
configuration's runs came from: 20 over 20 runs shows that every pair ran on instances of its
own. `--fresh-ssh` and `--skip-pairs` exist only in `run_ab.sh`, not in `run_ab.ps1`.

**If the series stops** (a failed `ssh`, a run that `measure.py` aborts), the complete pairs
stay valid. If the last pair has only one run in `data/fresh/`, move that run's `.json` and
`_latencies.csv` to `data/fresh/aborted/`. Then resume with the same command plus
`--skip-pairs <number of complete pairs>`: the remaining pairs get the seeds and order they
would have had, and on fresh instances, since the recreate comes first. Write the interruption
in the session log.

**A pair with a run that `measure.py` warned about** (D4: the run did not keep to the rate) is
redone the same way at the end of the series: move both of its runs to `data/fresh/aborted/`,
then measure pair *k* alone with `--skip-pairs <k-1> --repeat <k>`. Pairs are redone only for
reasons like these, never because of their energy figures (KUVIOT_JA_TAULUKOT.md, section 3).

## Tearing down

```bash
docker compose -f thesis/measurements/docker-compose.measure.yaml down
nmcli con down teastore-lan      # the port goes back to "Wired connection 1"
nmcli radio wifi on
sudo systemctl start gdm         # the desktop back; Ctrl+C the energy server first
sudo cpupower frequency-set -g powersave
# What sut_session.sh --quiet stopped; a reboot does the same. Snap apps, Firefox
# included, do not start without snapd.
sudo systemctl start snapd.socket snapd.service bluetooth.service cups.service \
    apt-daily.timer apt-daily-upgrade.timer fwupd-refresh.timer motd-news.timer man-db.timer
```

Between sessions on consecutive days, keep the stack up and leave out the first two lines.

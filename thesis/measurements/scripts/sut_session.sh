#!/usr/bin/env bash
# Prepare the SUT laptop for a measurement session and record the state it starts from.
#
# Run on the SUT after the stack and rapl_server.py are up (SETUP.md, D1), before the noise
# check. It
#   1. checks the conditions every run assumes: AC, battery not charging, charge limit,
#      turbo off, performance governor, Wi-Fi off, no desktop session, stack up and done
#      generating images, energy server answering;
#   2. with --quiet, stops background services that wake the CPU on their own schedule
#      (updates, snap, firmware checks, printing, Bluetooth) until the next reboot;
#   3. saves the processes, services, timers and containers running, and the busiest
#      processes over 10 s of idle, so sessions can be compared when idle power differs;
#   4. waits until the CPU package temperature has settled, at least --settle-min minutes
#      (measurement plan, section 5.1), sampling package power as it goes. The median over
#      the last 5 minutes is the session's idle power, the reference to compare sessions by.
#
#   sudo ./sut_session.sh --quiet
#
# Root is needed for energy_uj and for stopping services. Output goes to
# validation/<date>/session_<time>/ (UTC) next to the noise check of the same session.

set -u

QUIET=0
SETTLE_MIN=10
MAX_WAIT_MIN=30
INTERVAL_S=30
STABLE_RANGE_C=1.0
QUIET_UNITS="unattended-upgrades.service packagekit.service snapd.service snapd.socket
fwupd.service fwupd-refresh.timer apt-daily.timer apt-daily-upgrade.timer motd-news.timer
man-db.timer cups.service cups-browsed.service bluetooth.service"

while [ $# -gt 0 ]; do
    case "$1" in
        --quiet) QUIET=1 ;;
        --settle-min) SETTLE_MIN="$2"; shift ;;
        *) echo "usage: sudo $0 [--quiet] [--settle-min minutes]"; exit 2 ;;
    esac
    shift
done
if [ "$(id -u)" -ne 0 ]; then
    echo "run with sudo: energy_uj and stopping services need root"; exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="$SCRIPT_DIR/../docker-compose.measure.yaml"
RAPL_URL="http://10.10.10.1:9100/energy"
# UTC, like the timestamps measure.py writes, so session folders and runs line up.
OUT="$SCRIPT_DIR/../validation/$(date -u +%F)/session_$(date -u +%H%M)"
mkdir -p "$OUT"
# The date folder is shared with the driver's ladder and noise runs; created as root, it
# would make the next git pull fail to write them.
chown "${SUDO_USER:-root}:" "$(dirname "$OUT")" "$OUT"
CHECKS="$OUT/checks.txt"
: > "$CHECKS"

record() { echo "$1" | tee -a "$CHECKS"; }
check() {  # check <description> <actual> <expected...>
    local what="$1" actual="$2"; shift 2
    for expected in "$@"; do
        if [ "$actual" = "$expected" ]; then record "OK    $what: $actual"; return; fi
    done
    record "WARN  $what: $actual (expected: $*)"
}
first() { cat $1 2>/dev/null | head -n1; }
# The invoking user's name is written as "user", so the snapshots carry no personal name into
# published data. top cuts names longer than 8 characters to 7 and a "+"; that form is
# replaced too. Root and the system users keep their names.
anonymize() {
    local name="${SUDO_USER:-}"
    if [ -z "$name" ] || [ "$name" = root ]; then cat; return; fi
    sed -E "s/(^|[[:space:]])(${name}|${name:0:7}\+)([[:space:]]|\$)/\1user\3/g"
}

# 1. Conditions
# The machine's model rather than its hostname, which may hold a person's name. Lenovo keeps
# the model in product_version.
record "# $(date -u -Is) $(first /sys/class/dmi/id/product_version) $(uname -r)"
check "AC online" "$(first '/sys/class/power_supply/A*/online')" 1
check "battery status" "$(first '/sys/class/power_supply/BAT*/status')" "Not charging" "Full"
check "charge limit (conservation_mode)" \
    "$(first '/sys/bus/platform/drivers/ideapad_acpi/*/conservation_mode')" 1
check "turbo disabled (no_turbo)" "$(first /sys/devices/system/cpu/intel_pstate/no_turbo)" 1
check "CPU governors" \
    "$(sort -u /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | tr '\n' ' ' | sed 's/ $//')" \
    performance
check "Wi-Fi" "$(nmcli radio wifi 2>/dev/null)" disabled
check "desktop session running" "$(pgrep -x -c 'gnome-shell|Xorg|Xwayland' >/dev/null && echo yes || echo no)" no
# Only this stack's containers count, and generation must be done: the endpoint answers
# false (HTTP 500) until then, and a run started before it measures image generation.
check "stack containers up" "$(docker compose -f "$COMPOSE" ps -q --status running | wc -l)" 6
check "image generation finished (8081 8082 8083)" "$(for port in 8081 8082 8083; do
    curl -s -m 5 "localhost:$port/tools.descartes.teastore.image/rest/image/finished"; echo
done | tr '\n' ' ' | sed 's/ $//')" "true true true"
# Ask the server itself: a matching process name alone does not show it is up and bound to
# the link, and pgrep -f also matches editors and shells whose command line names the file.
check "rapl_server.py answering on $RAPL_URL" \
    "$(curl -s -m 5 "$RAPL_URL" | grep -q '"package-0"' && echo yes || echo no)" yes
# Without the desktop nothing stops suspend; the energy server is started under
# systemd-inhibit (SETUP.md, A6), which blocks sleep and the lid while it runs.
check "sleep and lid blocked (systemd-inhibit)" \
    "$(systemd-inhibit --list --no-pager --no-legend | grep -q 'TeaStore measurement.*block' && echo yes || echo no)" yes

# 2. Background services
if [ "$QUIET" -eq 1 ]; then
    for unit in $QUIET_UNITS; do
        if systemctl is-active --quiet "$unit"; then
            systemctl stop "$unit" && record "STOP  $unit"
        fi
    done
fi

# 3. Snapshot
ps -eo pid,user:32,%cpu,%mem,etime,comm --sort=-%cpu | anonymize > "$OUT/processes.txt"
systemctl list-units --type=service --state=running --no-pager > "$OUT/services.txt"
systemctl list-timers --all --no-pager > "$OUT/timers.txt"
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' > "$OUT/containers.txt"
# The second iteration of top covers the 10 s between the two samples.
top -b -n 2 -d 10 -o %CPU | awk '/^top -/ { n++ } n == 2' | head -n 20 | anonymize > "$OUT/idle_top.txt"
record "busiest over 10 s idle: $(awk 'NR > 7 && NF { printf "%s %s%%, ", $12, $9 }' "$OUT/idle_top.txt" | head -c 200)"

# 4. Settle and sample idle power
PKG=""
for zone in /sys/class/powercap/intel-rapl:*; do
    [ "$(cat "$zone/name" 2>/dev/null)" = "package-0" ] && PKG="$zone"
done
TEMP_ZONE=""
for zone in /sys/class/thermal/thermal_zone*; do
    [ "$(cat "$zone/type" 2>/dev/null)" = "x86_pkg_temp" ] && TEMP_ZONE="$zone"
done
if [ -z "$PKG" ] || [ -z "$TEMP_ZONE" ]; then
    record "WARN  package-0 energy or x86_pkg_temp not found; skipping the settle phase"
    chown -R "${SUDO_USER:-root}:" "$OUT"; exit 1
fi

echo "elapsed_s,pkg_temp_c,pkg_watts" > "$OUT/settle.csv"
range=$(cat "$PKG/max_energy_range_uj")
prev=$(cat "$PKG/energy_uj")
elapsed=0
echo "settling: at least $SETTLE_MIN min, until the package temperature varies by <= $STABLE_RANGE_C C over 2 min"
while :; do
    sleep "$INTERVAL_S"
    elapsed=$((elapsed + INTERVAL_S))
    now=$(cat "$PKG/energy_uj")
    temp=$(awk '{ printf "%.1f", $1 / 1000 }' "$TEMP_ZONE/temp")
    watts=$(awk -v a="$prev" -v b="$now" -v r="$range" -v t="$INTERVAL_S" \
        'BEGIN { d = b - a; if (d < 0) d += r; printf "%.3f", d / 1e6 / t }')
    prev=$now
    echo "$elapsed,$temp,$watts" >> "$OUT/settle.csv"
    printf '\r%4d s  %5s C  %6s W' "$elapsed" "$temp" "$watts"
    spread=$(tail -n 5 "$OUT/settle.csv" | awk -F, '{ if (min == "" || $2 < min) min = $2; if ($2 > max) max = $2 }
        END { printf "%.1f", max - min }')
    if [ "$elapsed" -ge $((SETTLE_MIN * 60)) ] && awk -v s="$spread" -v l="$STABLE_RANGE_C" 'BEGIN { exit !(s <= l) }'; then
        break
    fi
    if [ "$elapsed" -ge $((MAX_WAIT_MIN * 60)) ]; then
        echo; record "WARN  temperature did not settle within $MAX_WAIT_MIN min (last 2 min spread $spread C)"
        break
    fi
done
echo
idle=$(tail -n 10 "$OUT/settle.csv" | cut -d, -f3 | sort -n | awk '{ v[NR] = $1 } END { print (NR % 2) ? v[(NR + 1) / 2] : (v[NR / 2] + v[NR / 2 + 1]) / 2 }')
record "settled after ${elapsed} s at $temp C; idle package power (median, last 5 min): $idle W"
record "Write this idle power and the room temperature into data/SESSIOLOKI.md."

chown -R "${SUDO_USER:-root}:" "$OUT"
grep -q '^WARN' "$CHECKS" && { echo "Some checks failed; see $CHECKS"; exit 1; }
exit 0

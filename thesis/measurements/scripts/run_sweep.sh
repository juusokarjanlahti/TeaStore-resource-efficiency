#!/usr/bin/env bash
# Rendered-cache budget sweep, run on the Linux load driver (figure K5).
#
# Bash port of run_sweep.ps1; same options (as --kebab-case) and same behaviour.
#
# Measures the cache-on container at budgets of 1-16 MiB, plus the cache-off container as the
# same-session reference, at the fixed rate of the main series. The cache-on container is
# recreated for every budget, so each point starts with an empty cache in a fresh JVM, and the
# budget travels in the JVM options that measure.py records with every run. Before measuring,
# the script checks from the state endpoint that the budget in effect is the intended one.
#
# Points run in a fixed random order, drawn once with Python's random.seed(20260929), so that
# budget size is not confounded with thermal drift over the session. Repetitions of one point
# run back to back; recreating the container between every run would add an hour of restarts.
# aggregate.py therefore warns that the configurations look sequential: expected here.
#
#   ./run_sweep.sh --rate 1000 --ssh 10.10.10.1
#       recreates the container over ssh (needs an ssh key and sshd on the SUT)
#   ./run_sweep.sh --rate 1000
#       prints the command to run on the SUT and waits for Enter instead
#
# Decisions and their grounds: TESTIPENKKI.md, section 6.

set -eu

RATE=""             # the rate of the main series (SETUP.md, D2)
SUT=10.10.10.1
SSH=""              # user@host for recreating the container; empty = manual
SUT_REPO="~/projects/TeaStore-resurssitehokkuus"
REPEAT=3
DURATION=120
WARMUP=30
COOLDOWN=60
ALPHA=0.8
PROCESSES=3
CONCURRENCY=8
IDLE=20

while [ $# -gt 0 ]; do
    case "$1" in
        --rate) RATE="$2" ;;
        --sut) SUT="$2" ;;
        --ssh) SSH="$2" ;;
        --sut-repo) SUT_REPO="$2" ;;
        --repeat) REPEAT="$2" ;;
        --duration) DURATION="$2" ;;
        --warmup) WARMUP="$2" ;;
        --cooldown) COOLDOWN="$2" ;;
        --alpha) ALPHA="$2" ;;
        --processes) PROCESSES="$2" ;;
        --concurrency) CONCURRENCY="$2" ;;
        --idle) IDLE="$2" ;;
        *) echo "unknown option: $1 (see the header of this script)"; exit 2 ;;
    esac
    shift 2
done
[ -n "$RATE" ] || { echo "--rate is required"; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ORDER=(8 1 off 16 2 4 3)    # MiB; do not reorder, see above
DEFAULT_BYTES=$((3 * 1048576))
ON="http://${SUT}:8081/tools.descartes.teastore.image"
OFF="http://${SUT}:8082/tools.descartes.teastore.image"
OUT="$SCRIPT_DIR/../data/sweep"

wait_finished() {
    # The endpoint answers 500 until generation is done, which curl -f turns into a failure.
    local deadline=$((SECONDS + 900))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if [ "$(curl -fsS --max-time 5 "$1/rest/image/finished" 2>/dev/null || true)" = true ]; then return; fi
        sleep 10
    done
    echo "$1 did not finish generating images within 15 minutes"; exit 1
}

cache_budget() {
    local budget
    budget="$(curl -fsS --max-time 10 "$1/rest/image/state" |
        sed -nE 's/.*Rendered Image Cache Size \(Bytes\): [0-9]+ \/ ([0-9]+).*/\1/p' | head -n1)"
    [ -n "$budget" ] || { echo "$1/rest/image/state reports no rendered-cache budget. Is this the thesis build?"; exit 1; }
    echo "$budget"
}

set_cache_budget() {
    local bytes="$1"
    local command="cd $SUT_REPO && RENDERED_CACHE_BYTES=$bytes docker compose -f thesis/measurements/docker-compose.measure.yaml up -d --force-recreate image-cache-on"
    if [ -n "$SSH" ]; then
        ssh "$SSH" "$command" || { echo "recreating image-cache-on over ssh failed"; exit 1; }
    else
        printf '\nOn the SUT, run:\n  %s\n' "$command"
        read -r -p "Press Enter once the command has returned"
    fi
    wait_finished "$ON"
    local actual
    actual="$(cache_budget "$ON")"
    [ "$actual" = "$bytes" ] || { echo "cache-on budget is $actual bytes, expected $bytes"; exit 1; }
    echo "cache-on budget is $bytes bytes"
}

wait_finished "$OFF"
load_args=(--rate "$RATE" --processes "$PROCESSES" --concurrency "$CONCURRENCY" --idle "$IDLE")

for point in "${ORDER[@]}"; do
    if [ "$point" = off ]; then
        label=sweep_off; target="$OFF"
    else
        set_cache_budget $((point * 1048576))
        label="sweep_${point}mib"; target="$ON"
    fi
    for ((i = 0; i < REPEAT; i++)); do
        sleep "$COOLDOWN"
        python3 "$SCRIPT_DIR/measure.py" --label "$label" --target "$target" \
            --energy-url "http://${SUT}:9100" --energy rapl --out "$OUT" \
            --duration "$DURATION" --warmup "$WARMUP" --zipf-alpha "$ALPHA" --seed $((i + 1)) \
            "${load_args[@]}" || { echo "measure.py failed for $label, repetition $i"; exit 1; }
    done
done

# Leave the container at the main series' budget; run_ab.sh refuses any other.
set_cache_budget "$DEFAULT_BYTES"
python3 "$SCRIPT_DIR/aggregate.py" --data "$OUT"

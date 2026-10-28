#!/usr/bin/env bash
# Interleaved A/B measurement, run on the Linux load driver.
#
# Bash port of run_ab.ps1; same options (as --kebab-case) and same behaviour, plus
# --fresh-ssh and --skip-pairs, which run_ab.ps1 does not have.
#
# Requires docker-compose.measure.yaml up on the SUT and rapl_server.py running there.
# The order of the pair alternates each repetition (baseline first, then treatment first,
# ...) so neither configuration always runs on a warmer machine. A cooldown between runs
# lets the laptop settle back towards the same thermal state before the next window.
#
# Two comparisons, each against the same baseline (port 8082: PNG, rendered cache off):
#   --compare cache   the rendered-image cache, cache_off vs cache_on (8081); results to data/
#   --compare format  the product image format, png vs jpeg (8083), cache off in both;
#                     results to data/format/
# They are separate green software patterns and are reported separately.
#
#   ./run_ab.sh --rate 1000 --processes 3 --concurrency 8 --idle 20
#       fixed 1000 req/s from 3 driver processes, idle power measured before every run.
#       Pick --rate with the ladder in SETUP.md, D2, so both configurations do the same work.
#   ./run_ab.sh --rate 1000 --processes 3 --concurrency 8 --idle 20 --alpha 0.6 --out ../data/alpha06
#       robustness check at a less favourable skew, kept apart from the main series
#   ./run_ab.sh --compare format --rate 1000 --processes 3 --concurrency 8 --idle 20
#   ./run_ab.sh --rate 1000 --processes 3 --concurrency 8 --idle 20 --fresh-ssh 10.10.10.1 --out ../data/fresh
#       a new JVM for every pair: both containers are recreated over ssh before each pair, and
#       one settling run per container goes to <out>/settle/, outside the results. Runs of
#       one configuration differ between JVM instances by about as much as the cache effect
#       (TESTIPENKKI.md, section 5), so each pair has to come from instances of its own.
#   ./run_ab.sh ... --repeat 20 --skip-pairs 13
#       resumes an interrupted series after its 13 complete pairs, with the seeds (14-20) and
#       the order of each pair it would have had. Move the run of an incomplete pair out of
#       <out> first: the script refuses to measure a seed that <out> already holds.

set -eu

COMPARE=cache
SUT=10.10.10.1
REPEAT=10          # pairs; see KUVIOT_JA_TAULUKOT.md section 3
SKIP_PAIRS=0       # pairs already measured, when resuming an interrupted series
DURATION=120
WARMUP=30
COOLDOWN=60
ALPHA=0.8
RATE=0             # 0 = closed-loop, as fast as responses return
PROCESSES=1
CONCURRENCY=4      # threads per process
IDLE=0             # seconds of idle power measured before each run; 0 = skip
CACHE_BYTES=3145728  # rendered-cache budget the cache_on container must report
OUT=""             # results directory; default data/ or data/format/
FRESH_SSH=""       # user@host: recreate both containers before every pair; empty = never
SUT_REPO="~/projects/TeaStore-resurssitehokkuus"

while [ $# -gt 0 ]; do
    case "$1" in
        --compare) COMPARE="$2" ;;
        --sut) SUT="$2" ;;
        --repeat) REPEAT="$2" ;;
        --skip-pairs) SKIP_PAIRS="$2" ;;
        --duration) DURATION="$2" ;;
        --warmup) WARMUP="$2" ;;
        --cooldown) COOLDOWN="$2" ;;
        --alpha) ALPHA="$2" ;;
        --rate) RATE="$2" ;;
        --processes) PROCESSES="$2" ;;
        --concurrency) CONCURRENCY="$2" ;;
        --idle) IDLE="$2" ;;
        --cache-bytes) CACHE_BYTES="$2" ;;
        --out) OUT="$2" ;;
        --fresh-ssh) FRESH_SSH="$2" ;;
        --sut-repo) SUT_REPO="$2" ;;
        *) echo "unknown option: $1 (see the header of this script)"; exit 2 ;;
    esac
    shift 2
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
load_args=(--processes "$PROCESSES" --concurrency "$CONCURRENCY" --idle "$IDLE")
if awk -v r="$RATE" 'BEGIN { exit !(r > 0) }'; then load_args+=(--rate "$RATE"); fi

base="http://${SUT}:%s/tools.descartes.teastore.image"
declare -A target mime service
case "$COMPARE" in
    cache)
        labels=(cache_off cache_on)
        target[cache_off]="$(printf "$base" 8082)"; target[cache_on]="$(printf "$base" 8081)"
        mime[cache_off]=png; mime[cache_on]=png
        service[cache_off]=image-cache-off; service[cache_on]=image-cache-on
        [ -n "$OUT" ] || OUT="$SCRIPT_DIR/../data"
        ;;
    format)
        labels=(png jpeg)
        target[png]="$(printf "$base" 8082)"; target[jpeg]="$(printf "$base" 8083)"
        mime[png]=png; mime[jpeg]=jpeg
        service[png]=image-cache-off; service[jpeg]=image-jpeg
        [ -n "$OUT" ] || OUT="$SCRIPT_DIR/../data/format"
        ;;
    *) echo "--compare must be cache or format"; exit 2 ;;
esac

wait_finished() {
    # The endpoint answers 500 until generation is done, which curl -f turns into a failure.
    local deadline=$((SECONDS + 900))
    while [ "$SECONDS" -lt "$deadline" ]; do
        [ "$(curl -fsS --max-time 5 "$1/rest/image/finished" 2>/dev/null || true)" = true ] && return
        sleep 10
    done
    echo "$1 did not finish generating images within 15 minutes"; exit 1
}

check_containers() {
    for label in "${labels[@]}"; do
        finished="$(curl -fsS --max-time 10 "${target[$label]}/rest/image/finished" 2>/dev/null || echo false)"
        [ "$finished" = true ] || { echo "$label image service has not finished generating images"; exit 1; }

        # Each container must serve product images in the format its label claims.
        image="$(curl -fsS --max-time 10 -X POST -H 'Content-Type: application/json' -d '{"7": "400x310"}' \
            "${target[$label]}/rest/image/getProductImages" |
            python3 -c 'import json,sys; print(json.load(sys.stdin)["7"])')"
        case "$image" in
            "data:image/${mime[$label]};base64,"*) ;;
            *) echo "$label does not serve ${mime[$label]} product images. Is the Compose file up to date?"; exit 1 ;;
        esac
    done

    if [ "$COMPARE" = cache ]; then
        # A budget sweep (run_sweep.sh) recreates the cache-on container with other budgets;
        # make sure the series runs at the intended one.
        state="$(curl -fsS --max-time 10 "${target[cache_on]}/rest/image/state")"
        if ! grep -Eq "Rendered Image Cache Size \(Bytes\): [0-9]+ / ${CACHE_BYTES}\b" <<<"$state"; then
            echo "cache_on budget is not $CACHE_BYTES bytes. Recreate it on the SUT with plain" \
                "'docker compose -f thesis/measurements/docker-compose.measure.yaml up -d --force-recreate image-cache-on'"
            exit 1
        fi
    fi
}

recreate_containers() {
    # The budget goes in as RENDERED_CACHE_BYTES, so --cache-bytes also works on fresh JVMs.
    local command="cd $SUT_REPO && RENDERED_CACHE_BYTES=$CACHE_BYTES docker compose -f thesis/measurements/docker-compose.measure.yaml up -d --force-recreate ${service[${labels[0]}]} ${service[${labels[1]}]}"
    ssh "$FRESH_SSH" "$command" || { echo "recreating the containers over ssh failed"; exit 1; }
    for label in "${labels[@]}"; do wait_finished "${target[$label]}"; done
    check_containers
}

measure() {  # measure <label> <repetition> <out> <run label>
    sleep "$COOLDOWN"
    python3 "$SCRIPT_DIR/measure.py" --label "$4" --target "${target[$1]}" \
        --energy-url "http://${SUT}:9100" --energy rapl --out "$3" \
        --duration "$DURATION" --warmup "$WARMUP" --zipf-alpha "$ALPHA" --seed $(($2 + 1)) \
        "${load_args[@]}" || { echo "measure.py failed for $4, repetition $2"; exit 1; }
}

[ "$SKIP_PAIRS" -ge 0 ] && [ "$SKIP_PAIRS" -lt "$REPEAT" ] ||
    { echo "--skip-pairs must be at least 0 and below --repeat ($REPEAT)"; exit 2; }
# aggregate.py pairs runs by seed, so a seed measured twice into one directory breaks the
# pairing. This stops a resumed series from repeating a pair, and a new series from landing
# on an old one.
for ((i = SKIP_PAIRS; i < REPEAT; i++)); do
    if grep -qE "^ *\"seed\": $((i + 1))\$" "$OUT"/run_*.json 2>/dev/null; then
        echo "$OUT already holds a run with seed $((i + 1)), pair $((i + 1)) of this series." \
            "Resume with --skip-pairs, or move the run of an incomplete pair out of $OUT."
        exit 1
    fi
done

check_containers

for ((i = SKIP_PAIRS; i < REPEAT; i++)); do
    if [ $((i % 2)) -eq 0 ]; then order=("${labels[0]}" "${labels[1]}"); else order=("${labels[1]}" "${labels[0]}"); fi
    if [ -n "$FRESH_SSH" ]; then
        # A recreated JVM runs above its steady state for the first few minutes; the first
        # run after every recreate in the budget sweep was about 1 % higher, with higher idle.
        recreate_containers
        for label in "${order[@]}"; do measure "$label" "$i" "$OUT/settle" "settle_$label"; done
    fi
    for label in "${order[@]}"; do measure "$label" "$i" "$OUT" "$label"; done
done

python3 "$SCRIPT_DIR/aggregate.py" --data "$OUT"

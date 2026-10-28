#!/usr/bin/env bash
# Validates the two-machine measurement setup from the Linux load driver.
#
# Works through the chain one link at a time and stops at the first failure, so the
# message points at the broken link: Python, network, RAPL, services, catalogue, cache
# flag, and finally a short measurement of each configuration. Nothing it produces is a
# result; the smoke runs are written to a temporary folder, not to data/.
#
# Bash port of check_setup.ps1; same stages, same options.
#
#   ./check_setup.sh --sut 10.10.10.1

set -u

SUT=10.10.10.1
FIRST_PRODUCT_ID=7
CATALOG_SIZE=100
SMOKE_SECONDS=15

while [ $# -gt 0 ]; do
    case "$1" in
        --sut) SUT="$2"; shift ;;
        --first-product-id) FIRST_PRODUCT_ID="$2"; shift ;;
        --catalog-size) CATALOG_SIZE="$2"; shift ;;
        --smoke-seconds) SMOKE_SECONDS="$2"; shift ;;
        *) echo "usage: $0 [--sut ip] [--first-product-id n] [--catalog-size n] [--smoke-seconds s]"; exit 2 ;;
    esac
    shift
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENERGY_URL="http://${SUT}:9100"
LABELS="cache_on cache_off"
declare -A TARGET=(
    [cache_on]="http://${SUT}:8081/tools.descartes.teastore.image"
    [cache_off]="http://${SUT}:8082/tools.descartes.teastore.image"
)

green=$'\e[32m'; yellow=$'\e[33m'; red=$'\e[31m'; reset=$'\e[0m'
stage() { printf '\n[%s]\n' "$1"; }
pass() { printf '  %sOK%s    %s\n' "$green" "$reset" "$1"; }
note() { printf '  %sNOTE%s  %s\n' "$yellow" "$reset" "$1"; }
fail() { printf '  %sFAIL%s  %s\n' "$red" "$reset" "$1"; exit 1; }

# package-0 energy in microjoules, empty if the energy server does not answer
package_energy() {
    curl -fsS --max-time 5 "$ENERGY_URL/energy" |
        python3 -c 'import json,sys; print(json.load(sys.stdin)["zones"]["package-0"]["energy_uj"])'
}

# cache_counter <target> <Hits|Misses>
cache_counter() {
    curl -fsS --max-time 10 "$1/rest/image/state" |
        sed -nE "s/.*Rendered Image Cache $2:[[:space:]]*([0-9]+).*/\1/p" | head -n1
}

# product_image <target> <product id>: the image string the service returns
product_image() {
    curl -fsS --max-time 10 -X POST -H 'Content-Type: application/json' \
        -d "{\"$2\": \"400x310\"}" "$1/rest/image/getProductImages" |
        python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$2"
}

# The endpoint answers 500 until generation is done, which curl -f turns into a failure.
finished() { [ "$(curl -fsS --max-time 10 "$1/rest/image/finished" 2>/dev/null)" = "true" ]; }

stage "1. Python"
version="$(python3 --version 2>&1)"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    fail "python3 3.8+ not found (got '$version')"
fi
command -v curl >/dev/null || fail "curl not installed (sudo apt install curl)"
pass "$version"

stage "2. Network link to $SUT"
for port in 9100 8081 8082 8083; do
    if ! timeout 3 bash -c "exec 3<>/dev/tcp/$SUT/$port" 2>/dev/null; then
        case "$port" in
            9100) fail "port 9100 closed: is rapl_server.py running on the SUT with --bind $SUT? Is ufw blocking it?" ;;
            *) fail "port $port closed: is docker-compose.measure.yaml up on the SUT?" ;;
        esac
    fi
    pass "port $port reachable"
done

stage "3. RAPL energy from the SUT"
summary="$(curl -fsS --max-time 5 "$ENERGY_URL/energy" | python3 -c '
import json, sys
p = json.load(sys.stdin)
print("SUT %s, %s, zones: %s" % (p["host"].get("model"), p["host"]["cpu_model"], ", ".join(p["zones"])))')" ||
    fail "$ENERGY_URL/energy did not return valid JSON"
pass "$summary"
before="$(package_energy)" || fail "could not read package-0 energy"
sleep 10
after="$(package_energy)" || fail "could not read package-0 energy"
idle_watts="$(awk -v a="$after" -v b="$before" 'BEGIN { printf "%.2f", (a - b) / 1e6 / 10 }')"
if ! awk -v w="$idle_watts" 'BEGIN { exit !(w > 0) }'; then fail "package-0 energy did not increase over 10 s"; fi
pass "idle package power $idle_watts W (no load, both image services idle)"
if awk -v w="$idle_watts" 'BEGIN { exit !(w > 8) }'; then
    note "high for an idle laptop: something else is running on the SUT (desktop, browser, image generation?)"
fi

stage "4. Image services"
for label in $LABELS; do
    target="${TARGET[$label]}"
    finished "$target" || fail "$label has not finished generating images yet; wait and rerun"
    [ -n "$(cache_counter "$target" Hits)" ] ||
        fail "$label reports no rendered-cache counters: this is not the thesis build (Docker Hub image?)"
    pass "$label ready, thesis build"
done

stage "5. Product catalogue"
last_product_id=$((FIRST_PRODUCT_ID + CATALOG_SIZE - 1))
for label in $LABELS; do
    target="${TARGET[$label]}"
    placeholder="$(product_image "$target" -1)"
    for product_id in "$FIRST_PRODUCT_ID" "$last_product_id"; do
        if [ "$(product_image "$target" "$product_id")" = "$placeholder" ]; then
            fail "$label serves the 'not found' image for product $product_id; the id range $FIRST_PRODUCT_ID..$last_product_id is wrong"
        fi
    done
    pass "$label has real images for products $FIRST_PRODUCT_ID..$last_product_id"
done

stage "6. Cache flag"
for label in $LABELS; do
    target="${TARGET[$label]}"
    start_hits="$(cache_counter "$target" Hits)"; start_misses="$(cache_counter "$target" Misses)"
    for _ in $(seq 20); do product_image "$target" "$FIRST_PRODUCT_ID" >/dev/null; done
    hits=$(($(cache_counter "$target" Hits) - start_hits))
    misses=$(($(cache_counter "$target" Misses) - start_misses))
    if [ "$label" = cache_on ] && [ "$hits" -lt 19 ]; then
        fail "cache_on: 20 identical requests gave only $hits hits"
    fi
    if [ "$label" = cache_off ] && [ $((hits + misses)) -ne 0 ]; then
        fail "cache_off: counters moved ($hits hits, $misses misses), so the cache is not disabled"
    fi
    pass "$label: 20 identical requests -> $hits hits, $misses misses"
done

stage "7. Smoke measurement ($SMOKE_SECONDS s each, not a result)"
smoke_dir="${TMPDIR:-/tmp}/teastore-smoke"
rm -rf "$smoke_dir"
for label in $LABELS; do
    python3 "$SCRIPT_DIR/measure.py" --label "smoke_$label" --target "${TARGET[$label]}" \
        --energy-url "$ENERGY_URL" --energy rapl --first-product-id "$FIRST_PRODUCT_ID" \
        --catalog-size "$CATALOG_SIZE" --duration "$SMOKE_SECONDS" --warmup 5 --out "$smoke_dir" ||
        fail "measure.py failed for $label"
done
for file in "$smoke_dir"/run_*.json; do
    line="$(python3 - "$file" <<'PY'
import json, sys
run = json.load(open(sys.argv[1]))
r = run["results"]
if r["errors"] > 0:
    print("FAIL %s: %s failed requests" % (run["label"], r["errors"]))
else:
    watts = r["energy_joules"]["package-0"] / r["wall_seconds"]
    print("OK %s: %s req/s, %.2f W package under load, %s J/req"
          % (run["label"], r["throughput_rps"], watts, r["joules_per_request"]))
PY
    )"
    case "$line" in
        FAIL*) fail "${line#FAIL }" ;;
        *) pass "${line#OK }" ;;
    esac
done

printf '\n%sSetup looks valid. Under-load power should be clearly above the idle figure; if it is not, the load is not reaching the SUT'"'"'s CPU.%s\n' "$green" "$reset"

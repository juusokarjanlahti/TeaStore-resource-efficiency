# Interleaved A/B measurement, run on the Windows load driver.
#
# Requires docker-compose.measure.yaml up on the SUT and rapl_server.py running there.
# The order of the pair alternates each repetition (baseline first, then treatment first,
# ...) so neither configuration always runs on a warmer machine. A cooldown between runs
# lets the laptop settle back towards the same thermal state before the next window.
#
# Two comparisons, each against the same baseline (port 8082: PNG, rendered cache off):
#   -Compare cache   the rendered-image cache, cache_off vs cache_on (8081); results to data\
#   -Compare format  the product image format, png vs jpeg (8083), cache off in both;
#                    results to data\format\
# They are separate green software patterns and are reported separately.
#
#   .\run_ab.ps1 -Rate 1000 -Processes 3 -Concurrency 8 -Idle 20
#       fixed 1000 req/s from 3 driver processes, idle power measured before every run.
#       Pick -Rate with the ladder in SETUP.md, D2, so both configurations do the same work.
#   .\run_ab.ps1 -Rate 1000 -Processes 3 -Concurrency 8 -Idle 20 -Alpha 0.6 -Out ..\data\alpha06
#       robustness check at a less favourable skew, kept apart from the main series
#   .\run_ab.ps1 -Compare format -Rate 1000 -Processes 3 -Concurrency 8 -Idle 20

param(
    [ValidateSet("cache", "format")]
    [string]$Compare = "cache",
    [string]$Sut = "10.10.10.1",
    [int]$Repeat = 10,       # pairs; see KUVIOT_JA_TAULUKOT.md section 3
    [int]$Duration = 120,
    [int]$Warmup = 30,
    [int]$Cooldown = 60,
    [double]$Alpha = 0.8,
    [double]$Rate = 0,        # 0 = closed-loop, as fast as responses return
    [int]$Processes = 1,
    [int]$Concurrency = 4,    # threads per process
    [int]$Idle = 0,           # seconds of idle power measured before each run; 0 = skip
    [long]$CacheBytes = 3145728,  # rendered-cache budget the cache_on container must report
    [string]$Out = ""         # results directory; default data\ or data\format\
)

$ErrorActionPreference = "Stop"
$loadArgs = @("--processes", $Processes, "--concurrency", $Concurrency, "--idle", $Idle)
if ($Rate -gt 0) { $loadArgs += @("--rate", $Rate) }
$base = "http://${Sut}:{0}/tools.descartes.teastore.image"
if ($Compare -eq "cache") {
    $labels = @("cache_off", "cache_on")
    $targets = @{ "cache_off" = $base -f 8082; "cache_on" = $base -f 8081 }
    $mime = @{ "cache_off" = "png"; "cache_on" = "png" }
    if (-not $Out) { $Out = Join-Path $PSScriptRoot "..\data" }
} else {
    $labels = @("png", "jpeg")
    $targets = @{ "png" = $base -f 8082; "jpeg" = $base -f 8083 }
    $mime = @{ "png" = "png"; "jpeg" = "jpeg" }
    if (-not $Out) { $Out = Join-Path $PSScriptRoot "..\data\format" }
}

foreach ($label in $labels) {
    # The endpoint answers 500 until generation is done, which Invoke-RestMethod throws on.
    try { $finished = Invoke-RestMethod "$($targets[$label])/rest/image/finished" } catch { $finished = $false }
    if ("$finished" -ne "true") { throw "$label image service has not finished generating images" }

    # Each container must serve product images in the format its label claims.
    $body = '{"7": "400x310"}'
    $image = (Invoke-RestMethod "$($targets[$label])/rest/image/getProductImages" -Method Post `
        -ContentType "application/json" -Body $body)."7"
    if (-not "$image".StartsWith("data:image/$($mime[$label]);base64,")) {
        throw "$label does not serve $($mime[$label]) product images. Is the Compose file up to date?"
    }
}

if ($Compare -eq "cache") {
    # A budget sweep (run_sweep.ps1) recreates the cache-on container with other budgets; make
    # sure the series runs at the intended one.
    $state = Invoke-RestMethod "$($targets['cache_on'])/rest/image/state"
    if ($state -notmatch "Rendered Image Cache Size \(Bytes\): \d+ / $CacheBytes\b") {
        throw ("cache_on budget is not $CacheBytes bytes. Recreate it on the SUT with plain " +
            "'docker compose -f thesis/measurements/docker-compose.measure.yaml up -d --force-recreate image-cache-on'")
    }
}

for ($i = 0; $i -lt $Repeat; $i++) {
    $order = if ($i % 2 -eq 0) { $labels } else { @($labels[1], $labels[0]) }
    foreach ($label in $order) {
        Start-Sleep -Seconds $Cooldown
        python "$PSScriptRoot\measure.py" --label $label --target $targets[$label] `
            --energy-url "http://${Sut}:9100" --energy rapl --out $Out `
            --duration $Duration --warmup $Warmup --zipf-alpha $Alpha --seed ($i + 1) @loadArgs
        if ($LASTEXITCODE -ne 0) { throw "measure.py failed for $label, repetition $i" }
    }
}

python "$PSScriptRoot\aggregate.py" --data $Out

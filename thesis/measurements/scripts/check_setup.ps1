# Validates the two-machine measurement setup from the Windows load driver.
#
# Works through the chain one link at a time and stops at the first failure, so the
# message points at the broken link: Python, network, RAPL, services, catalogue, cache
# flag, and finally a short measurement of each configuration. Nothing it produces is a
# result; the smoke runs are written to a temporary folder, not to data\.
#
#   powershell -ExecutionPolicy Bypass -File .\check_setup.ps1 -Sut 10.10.10.1

param(
    [string]$Sut = "10.10.10.1",
    [int]$FirstProductId = 7,
    [int]$CatalogSize = 100,
    [int]$SmokeSeconds = 15
)

$ErrorActionPreference = "Stop"
$energyUrl = "http://${Sut}:9100"
$targets = [ordered]@{
    "cache_on"  = "http://${Sut}:8081/tools.descartes.teastore.image"
    "cache_off" = "http://${Sut}:8082/tools.descartes.teastore.image"
}

function Stage($title) { Write-Host ""; Write-Host "[$title]" }
function Pass($message) { Write-Host "  OK    $message" -ForegroundColor Green }
function Note($message) { Write-Host "  NOTE  $message" -ForegroundColor Yellow }
function Fail($message) { Write-Host "  FAIL  $message" -ForegroundColor Red; exit 1 }

function Get-PackageEnergy {
    $zones = (Invoke-RestMethod "$energyUrl/energy").zones
    return [double]$zones."package-0".energy_uj
}

function Get-CacheCounters($target) {
    $state = Invoke-RestMethod "$target/rest/image/state"
    $counters = @{}
    foreach ($name in "Hits", "Misses") {
        if ($state -match "Rendered Image Cache ${name}:\s*(\d+)") { $counters[$name] = [long]$Matches[1] }
    }
    return $counters
}

function Get-ProductImage($target, $productId) {
    $body = "{`"$productId`": `"400x310`"}"
    $response = Invoke-RestMethod "$target/rest/image/getProductImages" -Method Post `
        -ContentType "application/json" -Body $body
    return $response."$productId"
}

Stage "1. Python"
$version = ""
try { $version = (& python --version 2>&1) | Out-String } catch { }
if ($version -notmatch "Python 3\.(\d+)" -or [int]$Matches[1] -lt 8) {
    Fail "python 3.8+ not on PATH (got '$($version.Trim())'). If this opens the Microsoft Store, turn off the python.exe App execution alias."
}
Pass $version.Trim()

Stage "2. Network link to $Sut"
foreach ($port in 9100, 8081, 8082, 8083) {
    $open = Test-NetConnection -ComputerName $Sut -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
    if (-not $open) {
        switch ($port) {
            9100 { Fail "port 9100 closed: is rapl_server.py running on the SUT with --bind $Sut? Is ufw blocking it?" }
            default { Fail "port ${port} closed: is docker-compose.measure.yaml up on the SUT?" }
        }
    }
    Pass "port $port reachable"
}

Stage "3. RAPL energy from the SUT"
$payload = Invoke-RestMethod "$energyUrl/energy"
Pass "SUT $($payload.host.model), $($payload.host.cpu_model), zones: $(($payload.zones.PSObject.Properties.Name) -join ', ')"
$before = Get-PackageEnergy
Start-Sleep -Seconds 10
$idleWatts = ((Get-PackageEnergy) - $before) / 1e6 / 10
if ($idleWatts -le 0) { Fail "package-0 energy did not increase over 10 s" }
Pass ("idle package power {0:N2} W (no load, both image services idle)" -f $idleWatts)
if ($idleWatts -gt 8) { Note "high for an idle laptop: something else is running on the SUT (desktop, browser, image generation?)" }

Stage "4. Image services"
foreach ($label in $targets.Keys) {
    $target = $targets[$label]
    try { $finished = Invoke-RestMethod "$target/rest/image/finished" } catch { $finished = $false }
    if ("$finished" -ne "true") { Fail "$label has not finished generating images yet; wait and rerun" }
    $counters = Get-CacheCounters $target
    if (-not $counters.ContainsKey("Hits")) {
        Fail "$label reports no rendered-cache counters: this is not the thesis build (Docker Hub image?)"
    }
    Pass "$label ready, thesis build"
}

Stage "5. Product catalogue"
$lastProductId = $FirstProductId + $CatalogSize - 1
foreach ($label in $targets.Keys) {
    $target = $targets[$label]
    $placeholder = Get-ProductImage $target -1
    foreach ($productId in $FirstProductId, $lastProductId) {
        if ((Get-ProductImage $target $productId) -eq $placeholder) {
            Fail "$label serves the 'not found' image for product $productId; the id range $FirstProductId..$lastProductId is wrong"
        }
    }
    Pass "$label has real images for products $FirstProductId..$lastProductId"
}

Stage "6. Cache flag"
foreach ($label in $targets.Keys) {
    $target = $targets[$label]
    $start = Get-CacheCounters $target
    for ($i = 0; $i -lt 20; $i++) { Get-ProductImage $target $FirstProductId | Out-Null }
    $end = Get-CacheCounters $target
    $hits = $end["Hits"] - $start["Hits"]
    $misses = $end["Misses"] - $start["Misses"]
    if ($label -eq "cache_on" -and $hits -lt 19) { Fail "cache_on: 20 identical requests gave only $hits hits" }
    if ($label -eq "cache_off" -and ($hits + $misses) -ne 0) {
        Fail "cache_off: counters moved ($hits hits, $misses misses), so the cache is not disabled"
    }
    Pass "${label}: 20 identical requests -> $hits hits, $misses misses"
}

Stage "7. Smoke measurement ($SmokeSeconds s each, not a result)"
$smokeDir = Join-Path $env:TEMP "teastore-smoke"
Remove-Item $smokeDir -Recurse -Force -ErrorAction SilentlyContinue
foreach ($label in $targets.Keys) {
    python "$PSScriptRoot\measure.py" --label "smoke_$label" --target $targets[$label] `
        --energy-url $energyUrl --energy rapl --first-product-id $FirstProductId --catalog-size $CatalogSize `
        --duration $SmokeSeconds --warmup 5 --out $smokeDir
    if ($LASTEXITCODE -ne 0) { Fail "measure.py failed for $label" }
}
foreach ($file in Get-ChildItem $smokeDir -Filter "run_*.json") {
    $run = Get-Content $file.FullName -Raw | ConvertFrom-Json
    $results = $run.results
    if ($results.errors -gt 0) { Fail "$($run.label): $($results.errors) failed requests" }
    $watts = $results.energy_joules."package-0" / $results.wall_seconds
    Pass ("{0}: {1} req/s, {2:N2} W package under load, {3} J/req" -f `
        $run.label, $results.throughput_rps, $watts, $results.joules_per_request)
}

Write-Host ""
Write-Host "Setup looks valid. Under-load power should be clearly above the idle figure;" `
    "if it is not, the load is not reaching the SUT's CPU." -ForegroundColor Green

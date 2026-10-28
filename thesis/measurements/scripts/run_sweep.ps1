# Rendered-cache budget sweep, run on the Windows load driver (figure K5).
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
#   .\run_sweep.ps1 -Rate 1000 -Ssh <user>@10.10.10.1
#       recreates the container over ssh (needs an ssh key and sshd on the SUT)
#   .\run_sweep.ps1 -Rate 1000
#       prints the command to run on the SUT and waits for Enter instead
#
# Decisions and their grounds: TESTIPENKKI.md, section 6.

param(
    [Parameter(Mandatory = $true)]
    [double]$Rate,                  # the rate of the main series (SETUP.md, D2)
    [string]$Sut = "10.10.10.1",
    [string]$Ssh = "",              # user@host for recreating the container; empty = manual
    [string]$SutRepo = "~/projects/TeaStore-resurssitehokkuus",
    [int]$Repeat = 3,
    [int]$Duration = 120,
    [int]$Warmup = 30,
    [int]$Cooldown = 60,
    [double]$Alpha = 0.8,
    [int]$Processes = 3,
    [int]$Concurrency = 8,
    [int]$Idle = 20
)

$ErrorActionPreference = "Stop"
$order = @(8, 1, "off", 16, 2, 4, 3)    # MiB; do not reorder, see above
$defaultBytes = 3MB
$on = "http://${Sut}:8081/tools.descartes.teastore.image"
$off = "http://${Sut}:8082/tools.descartes.teastore.image"
$out = Join-Path $PSScriptRoot "..\data\sweep"

function Wait-Finished([string]$target) {
    # The endpoint answers 500 until generation is done, which Invoke-RestMethod throws on.
    $deadline = (Get-Date).AddMinutes(15)
    while ((Get-Date) -lt $deadline) {
        try { $finished = Invoke-RestMethod "$target/rest/image/finished" -TimeoutSec 5 } catch { $finished = $false }
        if ("$finished" -eq "true") { return }
        Start-Sleep -Seconds 10
    }
    throw "$target did not finish generating images within 15 minutes"
}

function Get-CacheBudget([string]$target) {
    $state = Invoke-RestMethod "$target/rest/image/state"
    if ($state -match "Rendered Image Cache Size \(Bytes\): \d+ / (\d+)") { return [long]$Matches[1] }
    throw "$target/rest/image/state reports no rendered-cache budget. Is this the thesis build?"
}

function Set-CacheBudget([long]$bytes) {
    $command = "cd $SutRepo && RENDERED_CACHE_BYTES=$bytes docker compose " +
        "-f thesis/measurements/docker-compose.measure.yaml up -d --force-recreate image-cache-on"
    if ($Ssh) {
        ssh $Ssh $command
        if ($LASTEXITCODE -ne 0) { throw "recreating image-cache-on over ssh failed" }
    } else {
        Write-Host "`nOn the SUT, run:`n  $command"
        Read-Host "Press Enter once the command has returned"
    }
    Wait-Finished $on
    $actual = Get-CacheBudget $on
    if ($actual -ne $bytes) { throw "cache-on budget is $actual bytes, expected $bytes" }
    Write-Host "cache-on budget is $bytes bytes"
}

Wait-Finished $off
$loadArgs = @("--rate", $Rate, "--processes", $Processes, "--concurrency", $Concurrency, "--idle", $Idle)

foreach ($point in $order) {
    if ("$point" -eq "off") {
        $label = "sweep_off"
        $target = $off
    } else {
        Set-CacheBudget ([long]$point * 1MB)
        $label = "sweep_${point}mib"
        $target = $on
    }
    for ($i = 0; $i -lt $Repeat; $i++) {
        Start-Sleep -Seconds $Cooldown
        python "$PSScriptRoot\measure.py" --label $label --target $target `
            --energy-url "http://${Sut}:9100" --energy rapl --out $out `
            --duration $Duration --warmup $Warmup --zipf-alpha $Alpha --seed ($i + 1) @loadArgs
        if ($LASTEXITCODE -ne 0) { throw "measure.py failed for $label, repetition $i" }
    }
}

# Leave the container at the main series' budget; run_ab.ps1 refuses any other.
Set-CacheBudget $defaultBytes
python "$PSScriptRoot\aggregate.py" --data $out

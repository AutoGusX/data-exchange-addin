# Starts the staged helper and checks it answers. No sign-in, no cloud calls.
#   powershell -ExecutionPolicy Bypass -File scripts\smoke-test.ps1 [-Mock]
param([switch]$Mock)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $repo 'dist\DataExchange\helper\FusionDxHelper.exe'
if (-not (Test-Path $exe)) { throw 'Helper not staged. Run scripts\build.ps1 first.' }

$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$secret = -join ($bytes | ForEach-Object { $_.ToString('x2') })

$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = $exe
$info.Arguments = 'serve --parent-pid ' + $PID + $(if ($Mock) { ' --mock' } else { '' })
$info.UseShellExecute = $false
$info.RedirectStandardOutput = $true
$info.CreateNoWindow = $true
$info.EnvironmentVariables['DXHELPER_SECRET'] = $secret
$process = [System.Diagnostics.Process]::Start($info)
try {
    $line = $process.StandardOutput.ReadLine()
    if (-not $line -or -not $line.StartsWith('DXHELPER_READY ')) { throw "Helper did not report ready: $line" }
    $port = ($line.Substring(15) | ConvertFrom-Json).port

    $health = Invoke-RestMethod "http://127.0.0.1:$port/v1/health"
    Write-Host ("health: helper {0}, mode {1}, SDK {2}, configured {3}, callback {4}" -f `
        $health.helperVersion, $health.mode, $health.sdkVersion, $health.configured, $health.callbackUrl)

    $refused = $false
    try { Invoke-RestMethod "http://127.0.0.1:$port/v1/hubs" | Out-Null } catch { $refused = $true }
    if (-not $refused) { throw 'Helper answered without the secret.' }
    Write-Host 'secret check: request without the secret was refused'

    $gated = $false
    try { Invoke-RestMethod "http://127.0.0.1:$port/v1/hubs" -Headers @{ Authorization = "Bearer $secret" } | Out-Null } catch { $gated = $true }
    if (-not $gated) { throw 'Helper listed hubs without a sign-in.' }
    Write-Host 'sign-in check: cloud call before sign-in was refused'
    Write-Host 'SMOKE TEST PASSED'
}
finally {
    if (-not $process.HasExited) { $process.Kill() }
}

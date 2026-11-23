# Copies dist\DataExchange into Fusion's AddIns folder. Run scripts\build.ps1 first.
# Stop the add-in in Fusion before deploying: a running helper locks its files.
#   powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repo 'dist\DataExchange'
if (-not (Test-Path (Join-Path $source 'helper\FusionDxHelper.exe'))) { throw 'Nothing to deploy. Run scripts\build.ps1 first.' }

$target = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\AddIns\DataExchange'
# /E copies subfolders; never /MIR. Exit codes below 8 mean success for robocopy.
robocopy $source $target /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE (is the add-in still running in Fusion?)" }
$global:LASTEXITCODE = 0
Write-Host "Deployed to $target"
Write-Host 'In Fusion: Utilities > Add-Ins > Scripts and Add-Ins > Add-Ins tab > DataExchange > Run.'

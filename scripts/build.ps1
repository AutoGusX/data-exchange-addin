# Builds and tests everything, then stages a deployable add-in in dist\DataExchange.
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 [-SkipTests]
param([switch]$SkipTests)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

Push-Location $repo
try {
    if (-not $SkipTests) {
        dotnet test helper\FusionDxHelper.slnx --nologo -v q
        if ($LASTEXITCODE -ne 0) { throw 'Helper tests failed.' }
    }

    $dist = Join-Path $repo 'dist'
    if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }
    $addin = Join-Path $dist 'DataExchange'

    # Self-contained: the helper carries its own .NET runtime, so the target machine needs nothing installed.
    dotnet publish helper\src\FusionDxHelper.Api\FusionDxHelper.Api.csproj -c Release --self-contained true --nologo -v q -o (Join-Path $addin 'helper')
    if ($LASTEXITCODE -ne 0) { throw 'Helper publish failed.' }

    Copy-Item (Join-Path $repo 'fusion_addin\DataExchange\DataExchange.py') $addin
    Copy-Item (Join-Path $repo 'fusion_addin\DataExchange\DataExchange.manifest') $addin
    New-Item -ItemType Directory -Force (Join-Path $addin 'lib') | Out-Null
    Copy-Item (Join-Path $repo 'fusion_addin\DataExchange\lib\*.py') (Join-Path $addin 'lib')
    Copy-Item (Join-Path $repo 'fusion_addin\DataExchange\resources') $addin -Recurse

    & $python -m py_compile (Get-ChildItem (Join-Path $repo 'fusion_addin\DataExchange') -Recurse -Filter *.py | ForEach-Object FullName)
    if ($LASTEXITCODE -ne 0) { throw 'Add-in Python does not compile.' }

    if (-not $SkipTests) {
        $env:DX_HELPER_EXE = Join-Path $addin 'helper\FusionDxHelper.exe'
        & $python -m unittest discover -s tests\integration
        if ($LASTEXITCODE -ne 0) { throw 'Add-in Python tests failed.' }
    }

    Write-Host "Staged add-in: $addin"
}
finally {
    Pop-Location
}

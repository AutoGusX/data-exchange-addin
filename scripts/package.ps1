# Zips dist\DataExchange for a second machine. Run scripts\build.ps1 first.
# By default the zip holds no credentials, tokens or client ID.
# -IncludeClientId puts a config.json with the client ID and callback URL from this machine's config
# inside the add-in folder (so the tester has nothing to configure),
# for handing to a named tester. The client ID is not a secret (PKCE, no client secret), but it is
# still kept out of git: the zip lives in dist\, which is ignored. Tokens are never included.
param([switch]$IncludeClientId)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repo 'dist\DataExchange'
if (-not (Test-Path (Join-Path $source 'helper\FusionDxHelper.exe'))) { throw 'Nothing to package. Run scripts\build.ps1 first.' }

# Read the version with a pattern: Windows PowerShell cannot parse the manifest (it has an empty key).
$manifest = Get-Content (Join-Path $source 'DataExchange.manifest') -Raw
if ($manifest -notmatch '"version"\s*:\s*"([^"]+)"') { throw 'No version in DataExchange.manifest.' }
$version = $Matches[1]
$suffix = ''
if ($IncludeClientId) { $suffix = '-with-client-id' }
$zip = Join-Path $repo "dist\DataExchange-addin-$version$suffix.zip"
if (Test-Path $zip) { Remove-Item $zip }

# The install guide travels inside the zip, next to the add-in folder.
$guide = Join-Path $repo 'dist\INSTALL.md'
$guideText = Get-Content (Join-Path $repo 'docs\install-windows.md') -Raw
$items = @($source, $guide)

$bundled = Join-Path $source 'config.json'
if (Test-Path $bundled) { Remove-Item $bundled }
if ($IncludeClientId) {
    $local = Join-Path $env:LOCALAPPDATA 'FusionDataExchange\config.json'
    if (-not (Test-Path $local)) { throw "No local config at $local." }
    $config = Get-Content $local -Raw | ConvertFrom-Json
    if (-not $config.clientId) { throw 'The local config has no clientId.' }
    # Only the client ID and callback URL are passed on, never the mock switch or local paths.
    # The file sits inside the add-in folder, where the add-in and the helper both look for it,
    # so the person installing it has nothing to configure.
    $shared = [ordered]@{ clientId = $config.clientId }
    if ($config.callbackUrl) { $shared.callbackUrl = $config.callbackUrl }
    [System.IO.File]::WriteAllText($bundled, ($shared | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
} else {
    $note = "> **This package has no APS client ID.** Before running the add-in, create ``config.json`` inside`r`n" +
            "> the ``DataExchange`` folder with ``{ `"clientId`": `"YOUR_APS_CLIENT_ID`" }``.`r`n`r`n"
    $guideText = $guideText -replace '(?m)^## Install', ($note + '## Install')
}
[System.IO.File]::WriteAllText($guide, $guideText, (New-Object System.Text.UTF8Encoding($false)))

Get-ChildItem $source -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Compress-Archive -Path $items -DestinationPath $zip
# The client ID must not linger in dist\DataExchange, which deploy.ps1 copies around.
if (Test-Path $bundled) { Remove-Item $bundled }
Write-Host "Package: $zip"
Write-Host 'Install steps: INSTALL.md inside the zip'

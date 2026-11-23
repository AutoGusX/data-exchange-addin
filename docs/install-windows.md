# Install on Windows

## Requirements

- Windows 10 or 11, 64-bit
- Autodesk Fusion
- An Autodesk account with access to the Forma (ACC) project that holds the exchange

Nothing else needs installing: the add-in brings its own helper and .NET runtime.

## Install

1. Unzip `DataExchange-addin-<version>.zip` anywhere you like (for example `Documents\Fusion add-ins`).
2. In Fusion: **Utilities > Add-Ins > Scripts and Add-Ins**, choose the **+** (add) button, pick
   **Script or add-in from device**, and select the unzipped `DataExchange` folder.
3. Select **DataExchange** in the list and choose **Run**. Turn on **Run on Startup** to have it load
   with Fusion.
4. A **Data Exchange** panel appears on the Automate tab of the Design workspace (on the Solid tab
   if this Fusion has no Automate tab). Choose **Account** and sign in with your own Autodesk account.

That is all. A packaged add-in carries its settings in `DataExchange\config.json`, so there is no
file to create. Each user signs in as themselves; the package holds no credentials or sign-in data.

Alternative to step 2: copy the `DataExchange` folder into
`%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\` and it appears in the list by itself.

## Settings (optional)

Settings are read from two files, the second overriding the first:

1. `config.json` inside the `DataExchange` folder (shipped with the package);
2. `%LOCALAPPDATA%\FusionDataExchange\config.json` (your own, only if you want to change something).

Keys: `"clientId"` (the APS app), `"callbackUrl"` (if the app registered a different one),
`"mock": true` (fixture data, no cloud), `"autoSave": false` (do not save the design after a load,
refresh or publish).

## One-time APS app setup (app owner, not each user)

1. In the APS app, enable **Data Management** and **Data Exchange**.
2. Add the callback URL `http://localhost:47821/` (with the trailing slash).
3. Ask the hub admin to approve the app under Hub Admin > Custom Integrations.

The app needs no client secret for this add-in. Do not put one anywhere.

## Use

- **Load Exchange:** open hub, project and folder in the tree and pick an exchange (a preview shows
  under the tree). The geometry arrives as a read-only reference component named
  `DX Reference — <exchange name>`, with its parts grounded. Save the design to keep the link.
  A Part document is converted to a Hybrid design first (the add-in asks).
- **Favorites:** hover a folder in the tree and click the star. Favorite folders are listed at the
  top of the tree in both Load and Publish. They are saved on this machine for your Windows user.
- **Refresh Exchange:** updates every loaded exchange in the design to its newest revision.
- **Publish Exchange:** publishes this design's own geometry as a new exchange in the folder you
  pick, or as a new revision of the exchange it published before.
- **Exchanges and Jobs:** opens the Data Exchange panel. Load, Refresh and Publish run there as
  background jobs with their results and a link to Autodesk Forma. The panel also lists what the
  design consumes and publishes; **Check for updates** compares them with the cloud.

This is a test build. Please report what fails, with the message text and its reference id.

## Uninstall

1. In Fusion, stop the add-in (Scripts and Add-Ins > DataExchange > Stop).
2. Delete `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\DataExchange`.
3. Delete `%LOCALAPPDATA%\FusionDataExchange` (config, logs, temporary files).

Designs keep their imported reference components; they are ordinary Fusion geometry.

## Troubleshooting

- **"helper is not installed" / "did not start":** the `helper` folder inside `DataExchange` is
  missing or incomplete. Unzip the package again; do not move files out of the folder.
- **"No APS client ID is configured":** this copy was packaged without a client ID. Add a
  `config.json` with `"clientId"` to the `DataExchange` folder, then stop and run the add-in.
- **Sign-in page shows a redirect error:** the callback URL on the APS app does not match exactly.
- **No hubs or projects listed:** the hub admin has not approved the app, or your account is not a
  project member.
- Logs: `%LOCALAPPDATA%\FusionDataExchange\logs`. Error messages carry a reference id that matches a
  log line.

## For developers

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1      # test, stage dist\DataExchange
powershell -ExecutionPolicy Bypass -File scripts\smoke-test.ps1 # helper answers, secret enforced
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1     # copy into Fusion's AddIns folder
powershell -ExecutionPolicy Bypass -File scripts\package.ps1    # zip for another machine
```

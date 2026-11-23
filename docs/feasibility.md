# Feasibility (spec 001, milestone 0)

Last updated 2026-11-16. **Status: milestone 0 gate PASSED in the console spike.** A real exchange was
read and exported to STEP, and a fixture was published, read back and re-published as a second
revision (see Observed results). Not yet done: opening the result in Fusion, a Revit-authored
exchange, and the two-user test. Items still marked *documented* have not been run live.

Legend: **Verified** = observed on this machine. **Documented** = stated by Autodesk, compiles, not run
live. **Open** = unknown until the spike runs.

## 1. SDK

| Item | Finding | Status |
|---|---|---|
| Package | `Autodesk.DataExchange` 8.1.0 on public nuget.org (versions listed: 8.0.0-alpha.1, 8.0.0-beta, 8.0.0, 8.1.0) | Verified (nuget.org index, restored here) |
| Optional UI package | `Autodesk.DataExchange.UI`, not used (the helper is headless) | Documented (package README) |
| Target frameworks | .NET Framework 4.8, `net8.0-windows7.0`, `net10.0-windows7.0` | Verified (nuspec + `lib/` folders) |
| Platform | Windows x64 only; "Any CPU" is not supported | Documented (package README) |
| Helper runtime | .NET 10 (SDK 10.0.401, runtime 10.0.12 on this machine), x64 | Verified (builds, helper starts, SDK 8.1.0 loads and reports its version) |
| Licence | Autodesk Platform Services terms (`LICENSE.txt` in the package) | Documented |
| Redistribution | The staged add-in ships the SDK binaries next to the helper. **Confirm the APS terms allow this before handing the zip to anyone outside Autodesk.** SDK binaries are not committed to git. | Open |
| Entitlement | Samples require an ACC account with membership on the hub and project; a hub admin must add the APS app under Custom Integrations | Documented (console sample README) |
| Reads exchanges | Read `Drawing1_Default` (14 elements, 2023 exchange) from an ACC project | Verified live. Source application of that exchange is unknown; a Revit-authored one is still to be tried |

## 2. Authentication

| Item | Finding | Status |
|---|---|---|
| Flow | `AuthOptions`: "ClientId and CallBack are required. Supplying ClientSecret selects the three-legged flow; without it, PKCE is used." The helper never sets a secret, so it uses PKCE with the system browser. | **Verified live 2026-11-16**: sign-in completed with a client ID only |
| Callback | `http://localhost:47821/` by default (override with `DX_CALLBACK_URL` or `callbackUrl` in the config file). Must be registered on the APS app exactly, with the trailing slash. | Our choice |
| APS app | Needs the Data Management and Data Exchange APIs enabled | Documented (console sample README) |
| Scopes | The helper leaves the SDK default. Token scopes observed: `openid code:all data:write data:read data:create bucket:create bucket:delete bucket:read account:write data:search user-profile:read` | Verified live. Broader than the add-in needs; narrowing via `AuthOptions.Scope` is a later hardening item |
| Token refresh | `EnableProactiveRefresh` / `RefreshTokenAsync` exist in the SDK; the helper relies on the SDK | Documented |
| Token storage | Not documented. The helper itself writes no token anywhere. | **Open: check whether the SDK caches a refresh token on disk, and where** |
| Sign out | The SDK has no sign-out call. The helper drops its client and session. If the SDK keeps a disk cache, sign-out must also clear it. | **Open** |
| Account identity | `GetUserAccountAsync` returned **403 Forbidden** live, although the token was valid and carries `user-profile:read`. The helper treats the profile as optional and signs in without a name. | **Open: find out why (APS app API access?)** |
| PKCE from a windowless process | The SDK opened the browser and received the redirect on port 47821 | Verified live (console spike; the Fusion-launched helper is still to be checked) |

## 3. Project access

Two test accounts with access to one Docs project and one exchange are not set up yet. **Open.**

## 4. Geometry

| Item | Finding | Status |
|---|---|---|
| Whole-exchange download | `IClient.DownloadCompleteExchangeAsSTEP` takes a **directory**, creates a uniquely named subfolder and returns the `.stp` path | Verified live (15.5 MB, 110 solids, 15.2 s for 14 elements) |
| Chosen path | **STEP**, imported with Fusion's own STEP importer. Reason for the intermediate file: it gives native Fusion solids with one supported call on each side, and the SDK places every element. | Decision |
| Mesh-only elements | The sample notes say a SAT download omits mesh geometry. Whether STEP does the same for mesh-only Revit elements is unknown. Fallback if so: OBJ download into Fusion mesh bodies. | **Open, the main geometry risk** |
| Units | Carried inside the STEP file. The SDK wrote the fixture download in **feet** (`CONVERSION_BASED_UNIT('foot')`, 304.8 mm); Fusion converts on import | Verified in the file (fixture 16000 mm long came back as 52.49 ft). Measuring it in Fusion is still owed |
| Per-element data | `ElementDataModel.Elements`: `SourceId`, `UniqueId`, `Name`, `HasGeometry` returned live. `Category` came back empty for every element in both exchanges read | Verified live; empty category is open |
| Element transforms | `Transformation.Matrix` layout (row or column major) is not documented. The manifest reports transforms as unknown with a `TRANSFORM_UNVERIFIED` warning instead of guessing. Import does not depend on them. | Open |
| Fusion import | `importManager.createSTEPImportOptions` + `importToTarget` into a new component | Documented Fusion API, **not yet run in Fusion** |

## 5. Revisions

| Item | Finding | Status |
|---|---|---|
| List revisions | `IClient.GetExchangeRevisionsAsync` returns `Id` and `LastModifiedUTC`; the helper takes the newest. The id changed after a second publish (1 then 2 revisions) | Verified live |
| Element id stability | Unknown. Refresh is a full replacement, so it does not rely on stable ids. | Open |
| Delta APIs | `RetrieveLatestExchangeAsync`, `GetCreated/Modified/DeletedElements` exist; not used in the MVP | Documented |

## 6. Publish (scope added 2026-11-16)

| Item | Finding | Status |
|---|---|---|
| Create | `CreateExchangeAsync(ExchangeCreateRequestACC)` with hub, project, folder, region, project type | Verified live (11.5 s) |
| Geometry | `ElementDataModel.CreateFileGeometry(path, GeometryFormat.Step, renderStyle, units)` + `AddElementGeometry` + `SyncExchangeDataAsync` | Verified live: 6 solids in, 6 solids back |
| Update | Load the model, `DeleteElementsBySourceId`, add the element again, sync | Verified live (9.2 s): still 1 element, 7 solids back, new revision id |
| Units argument | The helper passes millimetres with a millimetre STEP file; the size survived the round trip (16000 mm = 52.49 ft back). Not tested: a STEP file in other units | Verified for millimetres |
| Fusion export | `exportManager.createSTEPExportOptions(path, rootComponent)` | Documented Fusion API, not yet run |

## 7. Deployment

| Item | Finding | Status |
|---|---|---|
| Add-in location | `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\DataExchange` | Verified (other add-ins live there on this machine) |
| Helper packaging | Framework-dependent publish, staged in `helper\` inside the add-in folder; needs the .NET 10 Desktop Runtime x64 | Verified (builds, starts from the staged folder) |
| SDK service host | The SDK needs `DataExchangeServices\Autodesk.DataExchangeServices.exe` (geometry and parameter services, about 650 MB) next to the helper. The package build targets deploy it only to a project that references the package directly, so the Api project references it too. | Verified (first live run failed warm-up without it; now staged). The staged add-in is about 656 MB. |
| Fusion Python | The add-in uses only the standard library | Not yet run in Fusion |

## How to run the spike

1. Register the callback `http://localhost:47821/` on the APS app; enable Data Management and Data
   Exchange; have the hub admin approve the app under Custom Integrations.
2. `setx DX_CLIENT_ID <client id>` (or put `{"clientId": "..."}` in
   `%LOCALAPPDATA%\FusionDataExchange\config.json`), then open a new terminal.
3. `dist\DataExchange\helper\FusionDxHelper.exe spike`
4. Pick the hub, project, folder and exchange. The spike prints ids, revision, element counts by
   category, warnings, and writes the STEP file.
5. Paste the output below, open the STEP file in Fusion, and check the known dimension.

## Observed results

Mock run only (2026-11-16), which proves the pipeline but **not** cloud access:

```text
Data Exchange spike | helper 0.1.0 | mode mock | SDK n/a
MOCK MODE: fixture data only. This does not prove cloud access.
Exchange name : Building Core (mock)
Latest rev    : mock-rev-1 (), 1 revision(s)
Elements      : 6 (6 with geometry)
Geometry      : step, 34,692 bytes
Mock spike finished. Live integration is still unproven.
```

Live run 2026-11-16 (client ID configured, callback `http://localhost:47821/`):

```text
Data Exchange spike | helper 0.1.0 | mode sdk | SDK 8.1.0
Signing in (your browser will open)...
Signed in as your Autodesk account <profile not available>
Token scopes: openid code:all data:write data:read data:create bucket:create bucket:delete bucket:read account:write data:search user-profile:read
(14 hubs listed)
```

Read by Docs URL (`spike --url ...`), same day:

```text
Hub           : <test hub> [US]
Project       : <test project> (ACC)
Exchange name : Drawing1_Default
Latest rev    : 1695170890318_5a0344f0-... (2023-09-20 00:48:37Z), 1 revision(s)
Elements      : 14 (14 with geometry)
  sample: Pallet Rack Light:4 | Wall:15 | Wall:14 | Pallet Rack Light:5 | Wall (Right):1
Warnings      : 14 TRANSFORM_UNVERIFIED
Geometry      : step, 15,536,895 bytes (110 solids)
Export time   : 15.2 s
READ PASSED
```

Publish (`spike --url ... --publish rev1.step --publish2 rev2.step`) into the same folder, with
Gus's approval. This created the exchange **DX add-in test fixture** there:

```text
Published 'DX add-in test fixture' in 11.5 s, revision 1794846485843_3433333b-...
Elements      : 1 (1 with geometry)   Geometry: step, 46,129 bytes (6 solids)   Export time: 2.5 s
Updated in 9.2 s, revision 1794846495235_2dd16bd5-...   2 revision(s)
Revision id changed: a refresh would pick this up.
Elements      : 1 (1 with geometry)   Geometry: step, 53,256 bytes (7 solids)   Export time: 4.0 s
PUBLISH PASSED
```

Other things seen live: the SDK writes progress lines (`GetSchemasAsync: ...`) to stdout, so the
add-in now drains the helper's output pipes; the SDK needs its service host folder beside the helper.

### Live check of the spec 002 helper calls (2026-11-16, helper 0.2.0, test fixture only)

Driven through the helper's HTTP routes with the add-in's own client, against
`DX add-in test fixture`:

- **Verified:** sign-in; the exchange's `fileUrn` is a lineage URN (`urn:adsk.wipprod:dm.lineage:…`),
  so the Forma link opens the item, not just the folder.
- **Verified:** one publish with two elements, each with its own STEP file, and 5 custom instance
  parameters: accepted with no warnings, 19.4 s, new revision (3 revisions).
- **Verified:** `changes` since the previous revision reported the 2 created elements by name, 0
  modified, 0 deleted. The older `dx-addin-spike` element (not ours by prefix) was left in place.
- **Verified:** the progress relay: `/v1/status` returned step `FinalizeExchange` at 100% after the
  publish. Only the last step was sampled; the steps during a transfer were not observed.
- **Not seen here, confirmed later:** no pushed update notice arrived for the helper's own publish in
  about 10 s. Gus then confirmed in Fusion (2026-11-16) that notices do arrive when a second
  publisher updates the exchange, so the service does not notify the publisher of its own change.
- **Not checked:** how the parameters and the up axis look in Forma or Revit (needs eyes on the viewer).

### Live probe: properties on load and mesh scale (2026-11-16, read only)

- **Verified:** element instance parameters come back on load. The custom parameters published to
  the test fixture were read back with their values; `Drawing1_Default` elements carry `PartNumber`.
- **Verified:** the SDK reports a length unit per element: `Feet` for the fixture, `Centimeters` for
  `Drawing1_Default`.
- **Verified:** the whole-exchange OBJ download works (28 MB, 88,212 vertices for `Drawing1_Default`)
  and is in that same unit: the fixture is 12.139 high in both STEP (feet) and OBJ; `Drawing1_Default`
  measures 914.4 on one axis in both (centimetres).
- **Open:** axis order of the OBJ against the STEP was not settled (extents taken from STEP points
  include non-geometry points). A mesh-only exchange has not been found to test the fallback end to end.

## Blockers and what unblocks them

1. ~~APS client ID~~ done 2026-11-16 (sign-in and hub listing work).
2. **A Revit-authored exchange** for the spec's actual scenario (Revit is not installed on this
   machine). Reading and publishing are already proven with other exchanges.
3. **A second Autodesk account and Windows machine** for milestone 4.

## Sources

- `Autodesk.DataExchange` 8.1.0 package: README, nuspec, `Autodesk.DataExchange.xml` API docs
- https://github.com/autodesk-platform-services/aps-dataexchange-console
- https://github.com/autodesk-platform-services/aps-dataexchange-connector
- https://aps.autodesk.com/en/docs/dx-sdk/v8.0.0/developers_guide/overview/

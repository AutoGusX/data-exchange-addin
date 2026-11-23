# Architecture

```text
Fusion (Windows)
  fusion_addin/DataExchange          Python add-in: UI, Fusion document, metadata
        | HTTP on 127.0.0.1:<random port>, bearer secret per launch
  helper (FusionDxHelper.exe)        .NET 10 x64: sign-in, Autodesk Data Exchange SDK
        | HTTPS, the user's own PKCE token
  Autodesk Data Exchange service     exchanges and revisions in a Docs project
```

## Parts

| Part | Path | Job |
|---|---|---|
| Add-in entry | `fusion_addin/DataExchange/DataExchange.py` | Registers the toolbar panel |
| Commands | `lib/commands.py` | Account, Load Exchange, Refresh Exchange, Publish Exchange |
| Favorites | `lib/favorites.py` | Favorite folders, one list per user for Load and Publish. No Fusion imports. |
| Job queue | `lib/jobs.py` | Background jobs for Load, Refresh and Publish. No Fusion imports. |
| Jobs panel | `resources/jobs.html` | The "Data Exchange jobs" palette: progress and results |
| Explorer page | `resources/explorer.html` | Hub, project, folder and exchange tree shown in the Load and Publish dialogs |
| Document operations | `lib/fusion_ops.py` | STEP import and export, staged replacement, attributes. Main thread only. |
| Metadata | `lib/doc_meta.py` | Pure functions for the per-document records |
| Helper client | `lib/helper_client.py` | Starts the helper, talks to it. No Fusion imports. |
| Core | `helper/src/FusionDxHelper.Core` | Contracts, `IExchangeService`, manifest validation, mock service |
| SDK adapter | `helper/src/FusionDxHelper.AutodeskSdk` | The only code that calls the Autodesk SDK |
| API | `helper/src/FusionDxHelper.Api` | Loopback HTTP surface, config, console spike |

`IExchangeService` is the seam. `SdkExchangeService` is the real one; `MockExchangeService` serves the
fixtures in `tests/fixtures`. Mock data is always labelled: health says `mode: mock`, manifests say
`source: mock`, and the add-in prints "MOCK MODE" in its result messages.

## Helper process

- The add-in starts the helper on first use with `serve --parent-pid <Fusion pid>`. The helper exits
  when Fusion does.
- The add-in creates a random 256-bit secret and passes it in the `DXHELPER_SECRET` environment
  variable. The helper prints one line, `DXHELPER_READY {"port":N,"mode":"sdk"}`. The secret is never
  printed or logged.
- The helper binds to `127.0.0.1` on a random free port. Every route except `/v1/health` needs
  `Authorization: Bearer <secret>`.
- One signed-in Autodesk user per helper process. Sign out drops the session.

## Routes

Ids travel in the request body, not the path: one exchange is addressed by three opaque ids
(exchange, collection, hub), which the add-in stores and sends back unchanged.

| Route | Purpose |
|---|---|
| `GET /v1/health` | Helper and SDK version, mode, configured, signed in |
| `POST /v1/auth/login`, `POST /v1/auth/logout`, `GET /v1/auth/account` | Sign-in |
| `GET /v1/hubs`, `/v1/projects`, `/v1/folders`, `/v1/exchanges` | Browse |
| `GET /v1/exchanges/thumbnail` | Preview PNG for the Load window, written to a temp file; `available: false` when the exchange has none |
| `POST /v1/exchanges/resolve` | Folder listing entry to a full exchange reference |
| `POST /v1/exchanges/latest-revision` | Newest revision id |
| `GET /v1/status` | Transfer progress and pushed update notices, from the helper's memory |
| `POST /v1/events/watch` | Subscribe to new-revision notices for some exchanges |
| `POST /v1/exchanges/changes` | Elements created, modified and deleted after a given revision |
| `POST /v1/exchanges/export` | Download and normalize to a manifest plus geometry in a temp folder |
| `POST /v1/exchanges/export-mesh` | Same as export, with one mesh (OBJ) file: the fallback when STEP holds no bodies |
| `POST /v1/exchanges/publish` | Publish a STEP file as a new exchange or a new revision |
| `POST /v1/exchanges/delete` | Permanently delete an exchange (the add-in confirms with the user first) |
| `POST /v1/exports/cleanup` | Delete one export folder |
| `POST /v1/mock/advance` | Mock only: publish the next fixture revision |

Errors are `{code, message, correlationId}` (`contracts/errors.schema.json`).

## Additions in 0.3.0 (spec 003)
- `POST /v1/exchanges/data {hubId, projectId, fileUrn}`: element data of a data-only exchange, read by
  the helper from the AEC Data Model API (`AecDataModelReader.cs`) with the signed-in user's token. The
  token is used inside the helper only. The add-in keeps the result in the root attribute `dataOnly`.
- `ExchangeRef.region`: hub region, used to pick the Autodesk Forma web host for links.
- `ExportCache`: finished exports are kept under `exports\cache` per exchange and revision for the
  life of the helper process (wiped at start and stop, 2 GB cap). The export route asks for the latest
  revision first and serves a match from the cache.
- `lib/element_link.py` (pure): name matching between exchange elements and Fusion components and
  bodies. `fusion_ops.element_geometry` / `elements_of_selection` do the Fusion side.
- `doc_meta.parse_forma_link`: reads a pasted link; the load job finds the hub by listing.
- `favorites.json` also holds `last`, the folder last used to load or publish.

## Geometry

Whole-exchange **STEP**, both directions. The SDK writes one STEP file for the exchange; Fusion's STEP
importer turns it into solids inside the reference component. Publishing writes one STEP file per
published component (each exported from the root with only that component visible, so positions are
kept) and attaches each to its own element, with the component's name, part number, description,
material, mass and body count as custom parameters. Element source ids are built from the
component's permanent id and instance numbers (`publish_scope.unit_id`), so they survive renames.
More than 30 components go out as a single element. If the STEP file gives Fusion no bodies, the
exchange is downloaded again as a mesh (OBJ) and imported as a mesh body, scaled by the length unit
the exchange's elements report. See `docs/feasibility.md`.

## Conventions

- **Matrices in the manifest:** 4x4, row-major, translation in the last column (indices 3, 7, 11),
  the same layout as `adsk.core.Matrix3D.setWithArray`. Composition `Multiply(a, b)` applies `b` first.
- **Units:** Fusion's API works in centimetres. STEP files carry their own units and Fusion converts
  on import. Manifest transforms, when present, are in the exchange's units and must be scaled with
  `Transforms.ToCentimetres`. Element transforms are not exported yet (layout unverified).
- **Grounding:** every part inside the reference component is grounded to its parent on load and
  refresh, so the exchange stays together. The reference's own occurrence is not grounded.
- **Placement:** "As published" keeps source coordinates (default). "Move to origin" centres the
  reference on the origin in X/Y and puts its lowest point at Z = 0. The choice is stored and reused
  by refresh.

## Per-document metadata

Fusion attributes, group `DataExchangeAddin`:

- `reference` on each imported component: `schemaVersion`, `exchangeId`, `projectId`,
  `importedRevisionId`, `referenceGroupId`, `importMode`, `importedAtUtc`, the exchange reference,
  placement, counts.
- `elements` on each imported component: what the exchange contains (name, category and properties
  of up to 300 elements, plus the real total). Shown by the panel's Elements button.
- `published` on the root component: the exchange this design publishes to, the last revision, the
  saved file version it was published from, and the `scope` (whole design, or the entity tokens
  and names of the selected components and bodies). See `specs/002-…` and `lib/publish_scope.py`.

The imported component is found again through the attribute, not through a stored entity id. No
tokens are stored in the document.

After a load, a refresh that changed the design, or a publish, the add-in saves the design
(`Document.save`, a new file version described as "Data Exchange: …") so the identifiers are kept.
A design that has never been saved cannot be saved without asking where, so it is left alone and
the job result says to save it. `"autoSave": false` in `config.json` turns the save off.

## Refresh

1. Read the stored exchange reference and revision from the document.
2. Ask the helper for the newest revision. Same id: report "already up to date", change nothing.
3. Helper downloads and validates the manifest and geometry. Nothing in Fusion has changed yet.
4. Import into a new component named `… (updating)`.
5. If it holds geometry: delete the old reference component, rename the new one, write metadata.
6. On any failure: delete the new component; the old reference and its metadata stay.

Fusion's file import cannot run inside a command event (a documented API limitation), so each
command only collects its inputs and queues the document work, which runs on the main thread right
after the command closes (custom event `DX_DeferredWork`). The work is therefore not one Fusion
transaction: the staged replacement above is what protects the design, and reverting a refresh can
take more than one Undo.

## Jobs

Load, Refresh and Publish each queue a job and return at once, so Fusion stays usable. A job is a
generator that runs on the main thread and yields either `Background(label, work)` (network work on
a worker thread, one request at a time) or `WhenIdle(label)` (wait until Fusion is free). Document
work (import, STEP export, attributes) runs between yields, started from the `DX_DeferredWork`
custom event, and only when the job's own document is the active one and no other command is
running. Otherwise the job waits and is retried on `documentActivated` and `commandTerminated`;
the panel offers "Continue now" if only a command is in the way. A job is bound to the document it
was started from; closing that document fails the job. Results and errors are shown in the jobs
panel, not in message boxes. The import itself still blocks Fusion while it runs.

## Summary and alignment

The panel ("Exchanges and Jobs") lists what the active design consumes and what it publishes, read
from the per-document metadata. Alignment is a comparison of revision ids:

- **Consumed:** stored `importedRevisionId` against the exchange's latest revision in the cloud.
  Same: up to date. Different: a newer revision is available (Refresh).
- **Published:** stored `publishedRevisionId` against the latest revision. Different means the
  exchange was published again from somewhere else.
- **Local changes since publish:** the saved file version at publish time (`publishedFromVersion`)
  against the current version, plus unsaved changes. A hint only: it does not compare geometry.

The cloud is asked only by "Check for updates", by opening the panel from the toolbar, and by jobs;
results are kept for the Fusion session, not stored in the document.

## Explorer

Load and Publish pick their location in a tree (`Explorer` in `lib/commands.py`), an HTML page in a
Fusion browser command input. The page asks for a level when it is opened (`expand`), reports the
selection (`select`), and receives `children` and `preview` messages. Requests run on a worker
thread, one at a time, and the result returns to the main thread through the `DX_DeferredWork`
custom event, so the dialog does not freeze. The page holds no credentials and makes no network
calls of its own.

## Local files

Settings come from `config.json` inside the add-in folder (shipped with a packaged add-in) and then
`%LOCALAPPDATA%\FusionDataExchange\config.json`, which overrides it key by key; the helper reads the
same two files, then the environment.

`%LOCALAPPDATA%\FusionDataExchange\`: `config.json` (optional per-user settings),
`data-only.json` (file URNs and names of exchanges seen to be AEC Data Model based), `favorites.json` (favorite folders: hub, project and folder ids and names; `favorites.mock.json` in
mock mode),
`logs\` (helper log: correlation id, route, status, error code, duration; no tokens, no model data),
`exports\` (temporary geometry, cleared on helper start and stop).

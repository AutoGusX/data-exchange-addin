# Data Exchange add-in

Fusion add-in that **loads** Autodesk Data Exchanges (Revit-authored first) as read-only reference
geometry, **refreshes** them, and **publishes** a Fusion design as an exchange. Python add-in for the
Fusion side, a local .NET 10 helper for sign-in and the Autodesk Data Exchange SDK (8.1.0).
Owner: Gus Quade. Public repo `AutoGusX/data-exchange-addin` (MIT). The public history starts
from one clean commit (2026-11-23); the earlier working history lives on the local `dev-history`
branch. Never push that branch, and never commit hub, project or customer names, IDs or URNs. Commits
use the GitHub noreply address set in this repo's git config.

**Status 2026-11-23 (v0.2.0):** Gus runs it live in Fusion: load, refresh, publish (whole design or
selected components), jobs, automatic save and update notices were reported working on 2026-11-16.
Do not describe anything else as working in Fusion until `docs/VERIFICATION.md` says so. Live test
data sits in the owner's own test project: exchanges `Drawing1_Default` (read only) and
`DX add-in test fixture` (ours, safe to republish).

## Source of truth
- `specs/001-fusion-revit-data-exchange-mvp.md`: the MVP spec, with a scope amendment at the top
  (publishing from Fusion is IN scope; callback port 47821).
- `BACKLOG.md`: current work. Read it first; update it as work completes.
- `docs/feasibility.md`: what is verified vs only documented about the SDK. Update it with real spike output.
- `docs/VERIFICATION.md`: the in-Fusion checklist (no headless harness exists).
- `docs/architecture.md`, `docs/test-plan.md`, `docs/known-limitations.md`, `docs/install-windows.md`.

## Layout
- `fusion_addin/DataExchange/`: the add-in. `lib/commands.py` (UI), `lib/fusion_ops.py` (document, main
  thread only), `lib/doc_meta.py` and `lib/helper_client.py` (pure Python, no `adsk`, unit-tested).
- `helper/`: `FusionDxHelper.Core` (contracts, `IExchangeService`, mock), `FusionDxHelper.AutodeskSdk`
  (the ONLY code that calls the SDK), `FusionDxHelper.Api` (loopback HTTP + `spike` console), tests.
- `contracts/`: manifest and error schemas. `tests/fixtures/`: generated STEP fixtures.

## Run / verify
- `powershell -ExecutionPolicy Bypass -File scripts\build.ps1`: .NET tests, publish helper, stage
  `dist\DataExchange`, py_compile, Python tests against the real helper in mock mode.
- `scripts\smoke-test.ps1 [-Mock]`: staged helper starts and enforces its secret.
- `scripts\deploy.ps1`: robocopy `dist\DataExchange` into Fusion's AddIns folder (never `/MIR`). Stop the
  add-in in Fusion first; a running helper locks its files.
- Spike: `dist\DataExchange\helper\FusionDxHelper.exe spike [--url <Docs file URL>] [--publish a.step
  [--publish2 b.step] [--name N]]` (`--mock` for fixtures). It opens the browser; run with stdin from
  `/dev/null` when driving it from a tool. Publishing writes to a real project: ask Gus first.
- Config: `config.json` in the add-in folder (packaged builds), overridden by
  `%LOCALAPPDATA%\FusionDataExchange\config.json`: `clientId`, optional `callbackUrl`, `mock`, `autoSave`. Env `DX_CLIENT_ID` / `DX_CALLBACK_URL` override. Logs in `...\FusionDataExchange\logs`.
- Python: real 3.13 at `AppData\Local\Programs\Python\Python313\python.exe` (PATH `python` is a Store stub).

## Rules
- Never invent SDK calls. Check `~/.nuget/packages/autodesk.dataexchange/8.1.0/lib/net10.0-windows7.0/
  Autodesk.DataExchange.xml` or Autodesk's `aps-dataexchange-console` sample, and let the compiler confirm.
- PKCE only: never set `ClientSecret`, never store or log tokens, never put tokens in document attributes.
- Mock data must stay labelled (`mode: mock`, `source: mock`, "MOCK MODE" in messages). Never substitute
  local files for a real exchange silently.
- Helper stays loopback-only with the per-launch secret on every route except `/v1/health`.
- Refresh only ever touches the `DX Reference — …` component it owns; stage, then swap.
- Fusion API calls stay on the main thread; network waits go through `fusion_ops.run_blocking` with a
  `Progress` dialog shown first (waiting UI: show the window, then load).
- Event handlers go on `commands._handlers`.
- Network waits while a command dialog is open go through `commands._background(work, done)`
  (worker thread, result back on the main thread), not `run_blocking`.
- `importToTarget` fails inside ANY command event (InternalValidationError). Command execute handlers
  only read inputs and call `commands._defer(...)`; the document work runs from the custom event.
- Load, Refresh and Publish are jobs (`lib/jobs.py`): generators that yield `Background` for network
  work and `WhenIdle` before touching the document. Use `fusion_ops.design_of(document)`, never the
  active design, inside a job. Results go to the jobs panel, not message boxes.
- When the add-in starts sending the helper something an older helper would ignore, bump
  `HelperVersion` and `helper_client.REQUIRED_HELPER` together and check `helper_is_current` first.
- Do not commit SDK binaries, `dist/`, tokens, client IDs or customer model data.
- In shell heredocs, avoid apostrophes in file bodies (the tool mis-parses them); use the Write tool.

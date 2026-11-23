# Data Exchange add-in

A Fusion add-in that loads Autodesk Data Exchanges (for example, one published from Revit) into a
design as read-only reference geometry, refreshes them to the latest revision, and publishes a Fusion
design as a Data Exchange. A local .NET helper does the sign-in and the Autodesk Data Exchange SDK
work; the add-in owns the Fusion side.

**Status (2026-11-23, v0.3.0):** used live in Fusion by its author against Autodesk Forma projects:
load, refresh, publish (whole design or selected components), background jobs and update notices.
Windows only. Not yet tried with a Revit-authored exchange or on a clean machine; see
`docs/VERIFICATION.md` for what has and has not been checked.

This is sample code, provided as is under the MIT license. It is not an officially supported Autodesk
product. To run it you need your own APS client ID (a desktop/PKCE app, no client secret) with the
callback URL `http://localhost:47821/`; see `docs/install-windows.md`. The Autodesk Data Exchange SDK
is restored from NuGet at build time under its own terms and is not part of this repository.

AEC Data Model exchanges hold data only, with no geometry, and cannot be loaded as geometry; the add-in
says so, flags them, and tries to read their element data as a table instead.

- Spec: `specs/001-fusion-revit-data-exchange-mvp.md`
- How it fits together: `docs/architecture.md`
- Install and use: `docs/install-windows.md`
- Tests and acceptance status: `docs/test-plan.md`
- What it does not do: `docs/known-limitations.md`

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1    # test and stage dist\DataExchange
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1   # copy into Fusion
```

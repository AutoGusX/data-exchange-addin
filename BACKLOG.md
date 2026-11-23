# Backlog

Spec: `specs/001-fusion-revit-data-exchange-mvp.md`.

## Now (blocked on Gus)
- [ ] A Revit-authored test exchange in a Docs project (fixture in `docs/test-plan.md`).
- [ ] Run `docs/VERIFICATION.md` in Fusion: phase 2 live against `Drawing1_Default` and
      `DX add-in test fixture` (in the owner's test project), or phase 1
      with `"mock": true` in the config.

## Next
- [ ] Gus: paste the Forma link of the AEC Data Model exchange into Load Exchange. This is the one
      check that tells whether the AEC Data Model API lists such exchanges (spec 003, findings).
- [ ] Run the spec 003 section of `docs/VERIFICATION.md` in Fusion.
- [ ] Spec 002 remaining: link elements to Fusion bodies (per-element import).
- [ ] Open the spike's STEP output in Fusion and measure the fixture floor (expect 6000 mm).
- [ ] Delete the test exchange `DX add-in test fixture` from the ACC folder when no longer needed.
- [ ] Resolve the open items in `docs/feasibility.md`: SDK token cache and sign-out,
      mesh-only elements in the STEP download, empty element categories, element transform layout.
- [ ] `docs/VERIFICATION.md` phase 2 (live load, refresh, publish).
- [ ] Confirm hidden references are left out of the publish STEP export.
- [ ] Confirm SDK redistribution terms before sharing the package.
- [ ] Milestone 4: second account + machine, two-user acceptance script.

## Later
- [ ] Alignment: show who published the newer revision and when; flag references at startup.
- [ ] Explorer: reopen at the last used folder.
- [ ] OBJ/mesh fallback if STEP drops mesh-only elements.
- [ ] Show element names, categories and properties in Fusion.
- [ ] Publish per component with properties; choose what to publish.
- [ ] Paste a Docs URL in Load Exchange (the spike already resolves one; move `DocsUrl` behind a helper route).
- [ ] Measure time and memory on a moderately sized exchange.
- [ ] Compare with Autodesk's own Data Exchange connector for Fusion (positioning).

## Done
- [x] 2026-11-23 Spec 003 built as v0.3.0: paste a Forma link, last used folder, regional links,
      data-only element data (AEC Data Model API), element linking by name, session download cache.
      Unit tested; helper parts checked live read-only. Not run in Fusion.
- [x] 2026-11-23 AEC Data Model exchanges (data only, no geometry) are named as such when a load
      fails, and flagged in the explorer afterwards. Found in the helper log of 2026-11-17: the
      service answers "This API does not support AECDM based exchange". Not yet re-run in Fusion.
- [x] 2026-11-16 Gus's live pass in Fusion: everything tried works; pushed update notices confirmed
      with a second publisher; publish selection limited to components (single bodies failed and
      were dropped by decision). Account name lookup (403) accepted as is.
- [x] 2026-11-16 Simple install: settings shipped in the add-in folder, helper self-contained (no .NET
      install), install guide reduced to unzip, add folder in Fusion, run, sign in.
- [x] 2026-11-16 Spec 002 phase 6: search by name inside a project or folder, from the explorer.
- [x] 2026-11-16 Spec 002 phase 5: element list with properties stored on load and shown in the panel;
      mesh fallback when STEP holds no bodies; periodic revision recheck (5 min) for watched
      exchanges. Properties and mesh scale probed live; fallback itself untested end to end.
- [x] 2026-11-16 Spec 002 phases 4 and 6 (part): one element per published component with
      properties; delete an exchange from the Load tree. Compiled and mock-tested; live and
      in-Fusion checks owed.
- [x] 2026-11-16 Spec 002 phase 3 and part of 4: SDK progress on job lines, pushed update notices,
      viewer up axis on publish. Compiled and mock-tested; live and in-Fusion checks owed.
- [x] 2026-11-16 Spec 002 phase 2: revision changes (added, modified, deleted) in the summary and the
      refresh result; SDK status codes used for error mapping. Mock-tested; live check owed.
- [x] 2026-11-16 Spec 002 part A: publish a subset (selection remembered by entity token, export
      guard on solid count). In-Fusion check owed.
- [x] 2026-11-16 Automatic save after load, refresh and publish so the identifiers are kept
      (`autoSave` config switch; in-Fusion check owed).
- [x] 2026-11-16 Favorite folders shared by Load and Publish; "Autodesk Forma" renamed to "Autodesk
      Forma" in the UI and docs (in-Fusion check owed).
- [x] 2026-11-16 Summary of consumed and published exchanges with alignment against the cloud, in
      the "Exchanges and Jobs" panel (in-Fusion check owed).
- [x] 2026-11-16 Finished publish and load jobs link to the exchange in Autodesk Forma (live check owed).
- [x] 2026-11-16 Ground every part inside the reference on load and refresh (in-Fusion check owed).
- [x] 2026-11-16 Job queue: Load, Refresh and Publish run as background jobs with a docked jobs
      panel (`lib/jobs.py`, 7 unit tests). In-Fusion check owed.
- [x] 2026-11-16 Explorer tree for Load and Publish (hubs, projects, folders, exchanges, filter,
      reload, preview under the tree) replaces the dropdowns. Page logic checked in a headless
      browser with a fake Fusion bridge; in-Fusion check owed.
- [x] 2026-11-16 Load window preview: exchange thumbnail from the SDK metadata (verified live in the
      spike; in-Fusion display owed). Toolbar icon from the Data Exchange logo; panel on the Automate tab.
- [x] 2026-11-16 **Milestone 0 gate passed in the console spike:** read a real exchange by Docs URL
      and exported STEP; published the fixture as a new exchange, read it back, published revision 2.
      Fixed: STEP download path is a directory; add-in drains helper output.
- [x] 2026-11-16 Client ID configured locally; live PKCE sign-in and hub listing verified; SDK service
      host staged with the helper; profile lookup made optional (it returns 403).
- [x] 2026-11-16 Project groundwork: folder, CLAUDE.md, git, GitHub.
- [x] 2026-11-16 Spec 001 adopted; scope amended (publish from Fusion in; callback port 47821).
- [x] 2026-11-16 Helper: SDK adapter (compiles against 8.1.0), mock service, loopback API, console
      spike, 24 tests.
- [x] 2026-11-16 Add-in: Account, Load, Refresh, Publish commands; metadata; staged refresh; 10 Python
      tests against the real helper (mock).
- [x] 2026-11-16 Contracts, fixtures, build/deploy/package/smoke scripts, docs.

# 002: Subset publish and ideas from the Autodesk SDK samples

Status: built 2026-11-16 (parts A and B, phases 2 to 6), helper calls checked live; in-Fusion checks
are listed in `docs/VERIFICATION.md`. Still open: linking elements to Fusion bodies, faster refresh
from the SDK cache, STEP options, 3D preview. Owner: Gus Quade.

Sources: `autodesk-platform-services/aps-dataexchange-console` and `aps-dataexchange-connector`
(both on SDK 8.1.0, the version this add-in uses).

## Part A: publish a subset of the design

### Selecting

- Publish Exchange gets a **What to publish** choice: *Whole design* (default for a first publish) or
  *Selected components*.
- In selection mode the dialog shows a Fusion selection input. The user picks components
  (occurrences) in the canvas or the browser tree. Picking a component includes everything inside
  it at the moment of each publish.
- **Decision 2026-11-16 (Gus):** components only. Picking single bodies failed in Fusion and is not
  wanted; a stored selection that contains a body reports it as no longer usable.
- Anything inside a `DX Reference — …` component is refused: that is consumed data, not ours.

### Tracking the selection for future versions

- The `published` record on the root component gains a `scope`:
  `{"mode": "all"}` or `{"mode": "selection", "items": [{"token", "kind", "name"}]}`.
  `token` is the Fusion entity token, which survives saves, versions and renames.
- The next time Publish Exchange opens on that design, the stored mode is preselected and the stored
  items are put back into the selection input, so "publish the same things again" is one click. The
  user can add or remove items; the new set replaces the stored one.
- Items that no longer exist (deleted component or body) are reported in the dialog and in the job
  result. If none of the stored items exist, the user must choose again.
- New geometry added to the design later is **not** published automatically in selection mode unless
  it sits inside a selected component. The summary table shows the scope ("Whole design" or
  "N selected items") so this is visible.

### Exporting

- Same mechanism as today: everything that is not to be published is hidden for the moment of the
  STEP export, then shown again. No geometry is copied or changed.
- **Guard:** after the export the add-in counts the solids in the STEP file. If the file holds more
  solids than were meant to be published, the job fails and nothing is uploaded. This also settles
  the open question of whether hidden references are left out of a whole-design publish.

## Part B: ideas adopted from the samples

| Phase | Item | Notes |
|---|---|---|
| 2 | Revision changes: created, modified and deleted elements since the loaded revision | `GetCreatedElements` and friends. Shown when an update is available and in the refresh result. |
| 2 | Status codes for error mapping | `IStatusCodeException` |
| 3 | Real progress in jobs | `ProgressStepsManager` listener, relayed to the job line |
| 3 | Live update notice | `EventsController.SubscribeToExchangeUpdateEvent`; panel flags a new revision without a manual check |
| 4 | Viewer orientation on publish | `SetViewableWorldCoordinates` from the design's up axis |
| 4 | Publish structure and properties | One element per published component with name and properties; parameters |
| 5 | Properties on load; mesh fallback | Parameters and attributes into Fusion; OBJ download when STEP drops mesh-only elements |
| 6 | Delete an exchange; search by name | `DeleteExchangeAsync` behind a confirmation; search across a project |
| later | Faster refresh from the SDK cache; STEP options; 3D preview | `loadLatest`, `IsFlatSTEPAssembly` |

Not adopted: the ready-made connector window from the connector sample. It is a separate WPF window
and would replace the explorer, jobs panel and summary already built.

## Rules that still apply

- Every SDK call is confirmed by the compiler and, where it changes cloud data, by a live run against
  `DX add-in test fixture` only. Publishing anywhere else needs Gus's go-ahead.
- Each phase lands as its own commit with tests for the pure logic, and an entry in
  `docs/VERIFICATION.md` for what can only be checked in Fusion.

# Known limitations

As of 2026-11-16, version 0.1.0.

## Not proven yet

- **Live cloud runs so far are console-only.** The helper has read, exported and published real
  exchanges from the console spike (2026-11-16), but not yet when started by the add-in inside
  Fusion, and not yet with a Revit-authored exchange. See `docs/feasibility.md`.
- **Not run in Fusion.** The add-in's Python compiles and its non-Fusion parts are tested, but no
  command has been executed inside Fusion. See `docs/VERIFICATION.md`.

## By design in this MVP

- Windows only. One signed-in Autodesk user per Fusion session.
- Part documents hold one component, so Load Exchange offers to convert a Part to a Hybrid design
  first. Refresh and Publish do not need this.
- Refresh is manual and replaces the whole reference. No background polling, no per-element delta.
- Imported content is a normal Fusion component labelled `DX Reference — …`. Fusion has no hard
  lock, so it can be edited; edits are discarded by the next refresh without a warning.
- No Revit semantics: walls, doors and so on arrive as plain solids. Materials, annotations and
  most properties are not brought in. Element names and categories are read into the manifest but
  are not yet shown in Fusion.
- Publish sends the whole design as one element with one STEP file. No per-component elements, no
  properties, no selection of what to publish.
- A design publishes to one exchange at a time.
- Search walks the folders one request at a time and stops after 150 folders or 100 results, so a
  very large project may need the search started from a subfolder.
- The mesh fallback only runs when the STEP form gives no bodies at all. An exchange that mixes
  solids with mesh-only elements still loads the solids only. The element list is a record of what
  the exchange contains; elements are not linked to individual Fusion bodies.
- Publishing writes one STEP file per component, so a design with many top-level components takes
  longer to export and Fusion is busy for that time. Above 30 components it is sent as one element
  without per-component properties. Properties are text; mass is a low-accuracy estimate.
- Live update notices start only after a "Check for updates" in that Fusion session, and stop when
  Fusion closes. The up axis sent on publish comes from the Fusion modeling orientation preference,
  not from the individual design.
- Subset publish works by component: single bodies cannot be selected, and bodies that sit directly
  in the root go out only with a whole-design publish. Mesh bodies are not published.
- The automatic save after a load, refresh or publish saves the whole design, including any other
  unsaved edits, as a new version. A design that has never been saved is not saved automatically.
- Favorites are saved per Windows user on one machine; they do not follow you to another machine.
  A favorite whose folder was renamed keeps its old name until it is removed and added again; one
  whose folder was deleted shows an error when opened.
- Alignment compares revision ids, not geometry. "Changed since last publish" is based on saved file
  versions and unsaved changes, so it can flag a design whose published geometry did not change.
  Designs published before this was added have no version on record.
- The "Open in Autodesk Forma" link always uses `acc.autodesk.com`; projects in other regions (for
  example `acc.autodesk.eu`) are not handled yet.
- Jobs download and upload in the background, but the import (and the STEP export before a publish)
  still freezes Fusion while it runs. A job finishes only in the document it was started from.
  Jobs are not kept across a Fusion restart.
- Load and refresh are not a single undo step: Fusion's import cannot run inside a command, so the
  work runs just after the command closes. A failed load can leave a Part converted to Hybrid.

## Open risks

- **Mesh-only elements** may be missing from the STEP download. If the live fixture shows this, add
  the OBJ path as mesh bodies.
- **Publish units:** verified for millimetre STEP files only.
- **Element categories** came back empty from the SDK for every element read so far.
- **Account name:** the profile lookup returns 403, so the add-in cannot show who is signed in.
- **Package size:** about 656 MB, because the SDK's geometry service host must ship with the helper.
- **Hidden references on publish:** the add-in hides imported references before exporting the design
  so they are not republished. Whether Fusion's STEP export leaves hidden occurrences out is
  unverified.
- **Sign-out:** the SDK has no sign-out call and may keep its own token cache.
- **Element transforms** are not exported (matrix layout unverified).
- **Large models:** nothing is batched or streamed; time and memory are unmeasured.
- **SDK redistribution:** confirm the APS terms before sharing the package outside Autodesk.
- **Overlap:** check Autodesk's own Data Exchange connector for Fusion before positioning this.
- AEC Data Model exchanges hold data only, with no geometry, and the Data Exchange service refuses to
  read them through the SDK ("This API does not support AECDM based exchange"). They cannot be loaded.
  The service only says so when a load is tried, so an exchange is flagged "data only, no geometry" in
  the explorer after its first failed load on this machine, not before.

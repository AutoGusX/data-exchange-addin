# In-Fusion verification checklist

There is no headless Fusion harness, so everything here is done by hand in Fusion. Record the Fusion
build, Windows version and date with each pass.

**2026-11-16, Gus Quade, live in Fusion:** load, refresh, publish, jobs, subset publish by component,
publish per component, the automatic save and the panel were tried and reported working ("everything
else looks good"), and pushed update notices were confirmed with a second publisher. Selecting a
single body for publish failed and was removed by decision. Items are ticked below only where he
named the area; his sign-off was overall, not item by item, and the Fusion build was not recorded.
Not covered by that pass: mock mode (phase 1), a Revit-authored exchange, the mesh fallback, the
failure cases (no network, no project access), and a clean-machine install.

## Phase 1: mock mode (no client ID needed)

Setup: `%LOCALAPPDATA%\FusionDataExchange\config.json` contains `"mock": true`. Run the add-in.

- [ ] The Data Exchange panel appears on the Automate tab (Design workspace) with Account, Load Exchange, Refresh Exchange
      and Publish Exchange.
- [ ] Account: signs in as "Mock User"; the message says MOCK MODE; sign out works.
- [ ] Load Exchange when signed out offers to sign in; declining closes the command without an error.
- [ ] Load Exchange: hub, project and folder fill in; choosing "Project Files" lists
      "Building Core (mock)".
- [ ] Load: a component `DX Reference — Building Core (mock)` appears with 6 bodies. The result
      message shows revision `mock-rev-1`, 6 elements, 0 skipped, and the time.
- [ ] Scale: the floor's long edge measures 6000 mm. Placement: bounding box starts at
      (10000, 5000, 0) mm. The brace is rotated 30 degrees.
- [ ] Load with "Move to origin": the reference is centred on the origin and sits on Z = 0.
- [ ] Load and Publish show the explorer tree: hubs open to projects, folders and subfolders; each
      level shows "Loading…" while it loads and the dialog stays responsive. The filter box narrows
      the loaded names; the reload button refreshes the selected location.
- [ ] Favorites: hovering a folder shows a star; clicking it adds the folder to a Favorites list at
      the top of the tree. The same list appears in both Load and Publish, and after restarting
      Fusion. A favorite opens to its subfolders and exchanges, can be picked as the publish
      folder, and the star removes it again.
- [ ] Search: select a project or folder, type part of a name, press Enter. Load finds exchanges,
      Publish finds folders, in every folder below the selection; results show their path and can
      be picked like tree items. Close returns to the tree. With nothing selected it says so.
- [ ] Load: OK ("Load") is enabled only once an exchange is selected in the tree, and the line
      above the tree shows its full path. If OK stays greyed out after picking, record it here.
- [ ] Publish (new exchange): OK is enabled only with a writable folder selected; a read-only
      folder is marked and refused.
- [ ] Selecting the mock exchange shows "No preview available for this exchange." under the tree.
- [ ] In a Part document, Load Exchange asks to convert to Hybrid before browsing. Yes: the load
      succeeds and existing bodies are kept. No: the command closes and nothing changes.
- [x] Jobs: after clicking Load, the dialog closes at once, the "Data Exchange jobs" panel opens and
      shows the stages; Fusion stays usable (orbit, sketch) during the download.
- [x] Jobs: start a command (for example Extrude) during the download. The job shows "Waiting for
      the current Fusion command to finish" and imports when the command ends. "Continue now" works.
- [x] Jobs: switch to another document during the download. The job waits ("switch back to …") and
      finishes after switching back; nothing is imported into the other document.
- [x] Jobs: close the document during the download. The job fails with a clear message.
- [ ] Live publish: the finished job shows "Open in Autodesk Forma" and "Copy link". The link opens
      the published exchange in the right folder. Record whether it opens the item or only the folder.
- [ ] Summary: "Exchanges and Jobs" lists each consumed exchange (name, project, loaded revision,
      date, counts) and the published exchange. After the check: consumed shows "Up to date"; after
      a new revision is published elsewhere it shows "A newer revision is available" with a Refresh
      button that queues a refresh of just that reference.
- [ ] Panel layout: the exchanges are one table (type, exchange, status, revision, date, content,
      actions) that scrolls sideways in a narrow panel. Each job is a single line that expands on
      click; failed and waiting jobs start expanded.
- [ ] Revision changes (live): with a newer revision available, the table row shows "Since the loaded
      revision (1 revision): N added, N modified, N deleted", and the refresh job repeats it. Compare
      the numbers with what actually changed in the source model.
- [ ] Live progress: during a load or publish the job line shows the SDK's step and percent after the
      stage name (for example "Downloading the latest revision · Download geometry 40%"). Record
      the step names seen, or that none appear.
- [x] Live update notice (confirmed by Gus with a second publisher, 2026-11-16): after
      "Check for updates", publish a new revision from elsewhere, ideally as another user. Within a
      few seconds the row turns to "Update available" without another check.
- [ ] Viewer orientation: a published design sits upright in the Forma viewer, for a Y-up and a
      Z-up Fusion preference.
- [ ] Summary: after publishing, saving twice shows "Saved 2 time(s) since the last publish".
      Switching documents updates the summary. Signed out: "Sign in (Account) to check".
- [x] Automatic save: after a load, a refresh that updates, and a publish, a saved design gets a new
      version described "Data Exchange: …" and the job says so. Close without saving and reopen:
      the summary still lists the exchange. A never-saved design is not saved and the job says to
      save it. After a publish the summary does not claim the design changed since the publish.
- [x] Subset publish: choose "Selected components", pick two components, publish. The exchange in
      Forma holds only those. The job says "Published: 2 selected items". Bodies cannot be picked.
- [x] Subset publish: reopen Publish Exchange on the same design. The mode and the two items are
      already selected. Delete one of them and reopen: the dialog says it is not found any more.
- [x] Subset publish: nothing stays hidden in the design afterwards, also when the publish fails.
- [ ] Whole-design publish with a loaded exchange in the design succeeds (the guard confirms the
      reference was left out). If it fails with "exported more geometry than was meant", record it:
      Fusion's STEP export does not respect hidden items and the export approach must change.
- [x] Publish per component (live, test fixture only): a design with two components and a root body
      publishes as 3 elements; the job line reports elements and properties. In Forma each element
      shows its name and the properties (Part number, Material, Mass). Positions match the design.
      Republish after deleting one component: its element is gone from the exchange.
- [ ] Delete exchange: the bin icon on an exchange in the Load tree asks first; Yes removes it from
      the tree and from Forma; No changes nothing. Deleting the exchange this design publishes to
      removes the publish link from the design.
- [ ] Elements: after a load, the Elements button on the consumed row lists element names, categories
      and properties (for `Drawing1_Default`: 14 elements with PartNumber). A reference loaded
      before this existed says to refresh or reload.
- [ ] Mesh fallback (needs a mesh-only exchange): the load job says "No solid geometry: downloading
      it as a mesh", the reference holds a mesh body of the right size and orientation, and the
      result says "Imported as a mesh".
- [x] Jobs: Refresh and Publish show their result in the panel; a failure reopens a closed panel.
      The Jobs button reopens the panel; "Clear finished" empties it.
- [ ] After loading a multi-component exchange, no part inside the reference can be dragged away
      (each shows as grounded to parent); the whole reference still moves as one. The result shows
      "Parts grounded in the reference: N".
- [ ] Refresh on a reference loaded before grounding existed: "Already up to date. Grounded N parts".
- [ ] Loading the same exchange twice is refused with a pointer to Refresh.
- [ ] Unsaved design: the message warns that the link is kept only after saving.
- [ ] Save, close, reopen: Refresh Exchange still finds the reference.
- [ ] Refresh with no new revision: "already up to date", body count unchanged, nothing added to the
      timeline.
- [ ] Choose "Mock: publish next revision" (shown in mock mode only), then Refresh: the wall is
      3500 mm tall, there are 7 bodies, the name has no "(updating)" suffix, and only one reference
      component exists.
- [ ] Sketches and bodies outside the reference are unchanged after refresh. Note how many Undo
      steps a refresh takes (it is not a single transaction).
- [ ] Publish Exchange > New exchange: a design with its own body publishes; the message shows a
      revision. Publish again offers "Update …" and gives a new revision.
- [ ] Publish from a design that holds only a reference is refused ("no geometry of its own").
- [ ] Publishing does not include the reference component (check the exported STEP in the temp
      folder before it is removed, or load the published mock exchange back).
- [ ] A progress dialog is visible during every wait; Fusion stays responsive.
- [ ] Stop the add-in: the panel disappears and `FusionDxHelper.exe` exits. Closing Fusion also ends
      it.

## Phase 2: live (needs the client ID and the Revit test exchange)

- [ ] Account: the browser opens, sign-in completes, the message shows your name and email.
- [ ] Load a Revit-authored exchange: scale, orientation and placement match the fixture record in
      `docs/test-plan.md`.
- [ ] Load window: selecting `Drawing1_Default` in the tree shows its preview image (racks and walls); switching
      exchanges updates it; an exchange with no thumbnail shows "No preview available" and still loads.
- [ ] Elements skipped count is explained (mesh-only elements are the known risk).
- [ ] Publish revision N+1 from Revit, Refresh, and confirm the change.
- [ ] Publish from Fusion; open the exchange in the Docs viewer and check a known dimension (units).
- [ ] Sign out, then confirm the next cloud command asks for sign-in again, including after
      restarting Fusion.
- [ ] Wi-Fi off: Refresh reports a network failure and the reference is intact.
- [ ] An account without project access gets the access message, and no geometry.

## AEC Data Model (data-only) exchanges
- [ ] Load an AEC Data Model exchange. The job fails within seconds and says it is an AEC Data Model
      exchange with data only and no geometry, not a generic error. The design is unchanged.
- [ ] Open Load Exchange again: that exchange is tagged "data only, no geometry" in the tree, and
      selecting it shows the same note in place of the preview.

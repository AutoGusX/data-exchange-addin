# 003: Data-only exchanges, links, element linking and faster refresh

Status: built 2026-11-23 (v0.3.0), unit tested and partly checked live through the helper. Not run in Fusion. Asked for by Gus ("those are all worthy features. go").

Six additions, in build order. Each one says what is verified and what is not; nothing here has been
run in Fusion until `docs/VERIFICATION.md` says so.

## 1. Paste an Autodesk Forma link to load
- Load Exchange has a **Forma link** box. A pasted link to an exchange (project id in the path,
  `entityId` = the item's lineage URN) is loaded without browsing.
- The link names the project but not the hub, so the job looks through the hubs the account can see
  ("Finding the project from the link") and then resolves the exchange as usual.
- A folder link, or text that is not a Forma link, is explained in the status line and Load stays off.
- Only `https` links on Autodesk hosts are read. Parsing is pure Python (`doc_meta.parse_forma_link`).

## 2. Reopen the explorer at the last folder used
- The folder of the last load or publish is kept with the favorites (`favorites.json`, key `last`).
- When Load or Publish opens, that folder is listed first under **Last used**, selected and opened.
  In Publish this also preselects the target folder for a new exchange.

## 3. Forma links for other regions
- The link to an exchange is built for the region of its hub instead of always the US address.
- The region is stored with the exchange reference in the design, so links from older references
  (no region stored) keep using the US address.

## 4. Show AEC Data Model data instead of refusing it
- A data-only exchange cannot be loaded as geometry. Instead of only saying so, the add-in can read
  its element data through the AEC Data Model API and show it as a table in the panel.
- The helper makes the call with the signed-in user's token; the token never leaves the helper.
- Read-only. Nothing is written to the design or to the cloud.

## 5. Link elements to Fusion bodies
- In the element list of a loaded exchange, a row can select the matching geometry in the design, and
  the selected geometry can be traced back to its element row.
- Matching is by name between the exchange's elements and the components and bodies Fusion creates
  from the STEP file. Where names do not match, the add-in says so; it never guesses.

## 6. Faster refresh
- Avoid repeated cloud reads in one refresh, and reuse a download that is already on disk for the
  same revision.
- A true partial download (only changed elements) depends on what the SDK can export per element;
  see the findings section.

## Findings (2026-11-23, live through the helper, read-only)

**Element names against STEP names** (what part 5 rests on):
- Revit-style exchange (`Drawing1_Default`): elements are named `Wall:15`, `Pallet Rack Light:4`; the
  STEP products are `Wall_<32 hex digits>`, one per type, not per instance. A name therefore links an
  element to every placed copy of its type, and several elements share one piece of geometry.
- Inventor and SolidWorks exchanges: elements `Nut, Hex Regular:3`; products `Nut, Hex Regular`,
  `Nut, Hex Regular_1`, `Nut, Hex Regular_2`.
- Exchanges published by this add-in: the products are ids, but the solids carry the names
  (`00-Component3_1` for element `Component3:1`), so those link through body names.
- `element_link.py` removes exactly these decorations before comparing. Not every element has a
  namesake (for example `Cap Screw, Socket Head_&:29`); those report "nothing is named like it".

**Download time and the cache** (part 6):
- The SDK downloads the whole exchange every time: a second export of the same revision took as long
  as the first (11.7 s then 13.8 s for `Drawing1_Default`; 141 s then 157 s for a 150 MB exchange).
- With the session cache the second export of the same revision took 0.3 s instead of 15.8 s.
- The cache lives inside the export folder, which is wiped when the helper starts and stops, so no
  model data outlives the Fusion session. It is capped at 2 GB.
- A refresh to a NEW revision still downloads the whole exchange. The SDK's STEP export is
  whole-exchange only (`DownloadCompleteExchangeAsSTEP`); a partial download would need per-element
  import and per-element components in Fusion, which is a redesign and was not attempted. What a
  refresh saves now is the second read of the element data (compare, then download).

**AEC Data Model data** (part 4):
- `projectByDataManagementAPIId` rejected the `b.` project id as malformed (and the bare GUID), so the
  project is found by listing `hubs` and `projects` and matching their Data Management ids. That works.
- Page size 100 was refused for hubs and projects ("must be between 0 and 100"); 50 is used.
- The lookup of an element group by the exchange's lineage URN runs and returns nothing for ordinary
  exchanges, as expected. **Not verified:** that an AEC Data Model based exchange is listed there.
  No such exchange was in the folder that was probed, and Autodesk's documentation does not say
  either way. If it is not listed, the load job says the exchange is data only and that its data
  could not be read.

**Regions** (part 3): the helper now returns the hub region on the exchange reference (`US` seen live).
Host names per region are from Autodesk's ACC regions page; only the US link has been opened.

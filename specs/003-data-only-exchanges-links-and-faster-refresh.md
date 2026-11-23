# 003: Data-only exchanges, links, element linking and faster refresh

Status: in progress, 2026-11-23. Asked for by Gus ("those are all worthy features. go").

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

## Findings
(filled in as each part is checked)

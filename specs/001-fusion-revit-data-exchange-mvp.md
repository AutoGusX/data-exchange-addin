> **Scope amendment, 2026-11-16 (Gus).** This overrides the text below where they differ.
> 1. **Publishing from Fusion is in scope.** A Publish Exchange command exports the design and creates
>    an exchange, or a new revision of the exchange it published before. Sections 1.2 and 15.9 no
>    longer exclude it. Write-back into Revit objects stays out of scope.
> 2. **OAuth callback is `http://localhost:47821/`**, not port 8080.
> 3. **Repository layout** follows section 10 inside the existing repo `AutoGusX/data-exchange-addin`;
>    the add-in folder is `fusion_addin/DataExchange`.
> 4. **Geometry path** is whole-exchange STEP in both directions (see `docs/feasibility.md`), with
>    mesh as the fallback. Helper routes carry ids in the body (see `docs/architecture.md`).

# Fusion ↔ Revit Data Exchange Connector — MVP Engineering Specification

**Status:** Draft for coding-agent implementation  
**Date:** 2026-11-16  
**MVP direction:** Revit Data Exchange → Fusion, read-only, two independent users  
**Target platform:** Windows desktop Fusion; Autodesk-hosted Data Exchange in a shared Forma Data Management / Autodesk Docs project  
**Primary objective:** Two authorized Autodesk users, on separate computers and in separate Fusion designs, can import the same Revit-authored Data Exchange revision and manually refresh to the latest published revision.

> **Implementation caution:** SDK package names, method signatures, supported geometry representations, hosting-provider behavior, authentication requirements, and licensing must be verified against the currently installed Autodesk Data Exchange SDK and official documentation. This spec intentionally defines contracts and behaviors rather than inventing SDK methods. Do not silently substitute undocumented APIs.

## 1. Product outcome

A user installs a Fusion add-in and its local helper, signs in using their own Autodesk identity, selects a shared Data Exchange, and clicks **Load Exchange**. The add-in imports a recognizable, correctly positioned, read-only representation of the selected Revit geometry into the current Fusion design. A second user can do the same from a different machine and Autodesk account. Each user can click **Refresh Exchange** to retrieve a newer published revision.

**Success is independent consumption of the same cloud-published revision**, not real-time session mirroring or collaborative editing.

### 1.1 In scope

- Windows Fusion add-in with three UI operations: **Sign in / Sign out**, **Load Exchange**, **Refresh Exchange**.
- A local .NET companion process using the Autodesk Data Exchange SDK for cloud exchange discovery, reading, and revision retrieval.
- Per-user delegated authentication and authorization; no shared developer tokens.
- Browse or select a known shared project and exchange; a pasted exchange identifier/URL is acceptable as an initial fallback if discovery is blocked.
- Import supported geometry as **read-only reference content** in the active Fusion design, with a documented fallback for unsupported solid geometry.
- Preserve the exchange identifier, revision identifier, source element identifiers, and transforms for refresh and diagnostics.
- Manual refresh; report whether a newer revision is available.
- Two-user, two-computer acceptance test.
- Minimal install/uninstall instructions and structured diagnostics.

### 1.2 Explicitly out of scope

- Publishing Fusion geometry to Data Exchange; Revit-side write-back.
- Editing Revit source objects from Fusion.
- Real-time push updates, background polling, webhooks, or a custom hosted backend.
- Multi-user locking or concurrent exchange authoring.
- Reconstructing native parametric Revit semantics (walls, doors, MEP connectors) as editable Fusion features.
- Full-fidelity material appearance, annotations, sheets, families, constraints, and all Revit categories.
- macOS support in this MVP; automatic updates, enterprise deployment, or marketplace packaging.
- Hosting an exchange inside a Fusion hub.

## 2. Architecture

```text
User A: Fusion (Windows)                   User B: Fusion (Windows)
  Fusion Python add-in                       Fusion Python add-in
         | local IPC                                | local IPC
  .NET SDK helper                            .NET SDK helper
         | delegated auth / HTTPS                   | delegated auth / HTTPS
         +-------------------+-----------------------+
                             |
              Autodesk Data Exchange service
                             |
             Shared Forma / Autodesk Docs project
                     Revit-authored exchange
                     revision N, then N+1
```

- **Fusion add-in:** UI, active-document interaction, creation/replacement of reference geometry, local metadata persistence, error presentation.
- **Local helper:** Data Exchange SDK integration, auth broker if supported, exchange metadata and geometry retrieval, normalization into a versioned interchange payload.
- **Autodesk cloud:** Existing authoritative storage of exchange and its revisions. Do not implement an independent exchange database or service.
- **Fusion hub:** May store the user's Fusion document normally. It is *not* assumed to host Data Exchange objects. The add-in records a reference to the external exchange.

### 2.1 Runtime boundary

Fusion's add-in APIs and the .NET Data Exchange SDK may require different runtimes. **Do not attempt in-process .NET hosting inside Fusion for the MVP.** Run the helper as a separate local process, started on demand or by the add-in. Use localhost HTTP bound to `127.0.0.1` with a per-launch random bearer secret, or named pipes with equivalent access controls. Avoid a remotely accessible listening interface.

The helper must not directly mutate Fusion objects. Fusion API operations must run through the Fusion-supported execution/threading model; marshal responses to the appropriate Fusion event/UI thread as required.

## 3. Prerequisites and feasibility gates

Before implementing the geometry pipeline, create a short `docs/feasibility.md` with verified answers and links to primary documentation/sample code:

1. **SDK:** Exact Data Exchange SDK version, package identifiers, license/entitlement, supported Windows runtime, and ability to read Revit-authored exchanges.
2. **Auth:** Supported desktop delegated OAuth flow (prefer authorization code + PKCE/system browser), required APS scopes, token audience, refresh-token handling, and whether the SDK supplies its own auth mechanism. No client secret embedded in distributed binaries.
3. **Project access:** Two distinct test Autodesk accounts have access to the same Forma/Autodesk Docs project and exchange. Confirm both can retrieve the same exchange/revision.
4. **Geometry:** Which payloads are returned (B-rep, mesh, primitives, instances), how to obtain transforms and units, and which can be created with supported Fusion APIs.
5. **Revision semantics:** How to list/resolve the latest revision and detect whether element IDs remain stable across revisions.
6. **Deployment:** Fusion add-in discovery/install locations, supported Python version, helper executable packaging, and dependency/runtime installation requirements.

**Stop/go rule:** If SDK authentication or exchange geometry retrieval cannot be demonstrated in a standalone .NET console spike, pause add-in implementation and document the blocker. Do not fake an end-to-end integration.

## 4. User experience

### 4.1 Commands

**Sign in**
- Opens an Autodesk-supported delegated sign-in flow.
- Shows signed-in account identity (where API permits) and sign-out action.
- The user must not need to copy access tokens manually.

**Load Exchange**
- Choose a project and exchange (or paste an exchange identifier as a documented MVP fallback).
- Display exchange name, project, available/latest revision, and optional geometry summary.
- Import into a dedicated, clearly labeled Fusion component/group: `DX Reference — <Exchange Name>`.
- Show success with imported element count, skipped element count, revision, and elapsed time.

**Refresh Exchange**
- Available when a document contains a previously imported exchange.
- Resolve latest authorized revision and compare with the document's stored revision.
- If unchanged: show `Already up to date` without modifying geometry.
- If changed: stage a new representation and replace the previous imported reference atomically or via a recoverable two-phase operation.
- Never alter user-authored Fusion geometry outside the dedicated imported reference group.

### 4.2 Failure states

- Not signed in / expired credentials: offer re-authentication.
- No access / missing exchange: show clear project-permission or not-found message without exposing secrets.
- Unsupported geometry: skip with a count and diagnostic details; do not fail the entire import if other geometry is usable.
- Network unavailable: preserve existing imported geometry; retry manually.
- Partial import: retain prior revision until the new one is valid, or clearly mark partial state and provide recovery.
- Document unsaved: allow import if supported, but warn that refresh metadata persists only after saving the design.

## 5. Data contracts (internal, not SDK schemas)

Define a normalized JSON manifest and optional binary geometry blobs between helper and add-in. Version all schemas. Prefer streaming/chunking or local temporary files for large geometry instead of oversized IPC JSON payloads.

Example manifest (illustrative field names):

```json
{
  "schemaVersion": 1,
  "exchangeId": "opaque-exchange-id",
  "revisionId": "opaque-revision-id",
  "exchangeName": "Building Core",
  "sourceApplication": "Revit",
  "units": "meters",
  "coordinateSystem": "source-coordinate-description",
  "elements": [
    {
      "sourceElementId": "opaque-stable-id",
      "displayName": "Structural Column",
      "category": "Structural Columns",
      "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
      "geometry": { "kind": "mesh", "blobRef": "geometry/element-0001.bin" },
      "properties": { "sourceCategory": "Structural Columns" }
    }
  ]
}
```

**Conventions:** Document 4×4 matrix ordering, coordinate handedness, origin placement, transform composition, and source-to-Fusion unit conversion. Do not assume Revit internal feet, exchange units, and Fusion API units are identical. Do not assume SDK element IDs are stable until tested.

### 5.1 Per-document metadata

Store at minimum:

```json
{
  "schemaVersion": 1,
  "exchangeId": "opaque-exchange-id",
  "projectId": "opaque-project-id",
  "importedRevisionId": "opaque-revision-id",
  "referenceGroupId": "fusion-entity-identifier",
  "importMode": "read-only-reference",
  "importedAtUtc": "2026-11-16T00:00:00Z"
}
```

Use supported Fusion document attributes or other document-persistent metadata. If entity IDs are unstable after reopening, implement a robust lookup using attributes attached to the imported root. Do not persist OAuth tokens in document attributes. Keep the original exchange ID/revision as opaque strings.

## 6. Geometry strategy

### 6.1 MVP priority

1. **First:** Validated mesh/reference geometry import for common architectural elements (walls, floors, columns, beams), preserving world placement and instance transforms.
2. **Next, if straightforward:** B-rep to native Fusion solid where a supported, reliable conversion exists.
3. **Later:** More categories, large models, advanced properties, instance-aware deduplication, parametric reconstruction.

Do not assume Fusion's mesh API and B-rep API are interchangeable. Verify supported creation/import APIs. Avoid an intermediate file format unless needed; if required, document why and choose a supported, testable format.

### 6.2 Geometry correctness

- Preserve scale, orientation, origin, relative placement, and repeated instances.
- Provide a configurable import origin strategy if Revit shared coordinates cause extreme distances from Fusion's modeling origin; default behavior must be documented and deterministic.
- Keep all imported entities in a dedicated reference group/component.
- Where possible mark imported content non-editable/non-participating in downstream manufacturing operations; otherwise clearly label it as reference-only and avoid promising hard edit protection.
- Record warnings for geometry that is missing, degenerate, invalid, excessively large, or unsupported.

## 7. Local helper interface

Use an explicit versioned contract. Example conceptual endpoints (not Autodesk SDK methods):

| Endpoint | Purpose |
|---|---|
| `GET /v1/health` | Helper and SDK version, readiness |
| `POST /v1/auth/login` | Initiate supported delegated sign-in |
| `POST /v1/auth/logout` | Clear local session credentials |
| `GET /v1/projects` | List authorized compatible projects, if supported |
| `GET /v1/exchanges?projectId=...` | List accessible exchanges, if supported |
| `GET /v1/exchanges/{id}/revisions/latest` | Resolve latest revision |
| `POST /v1/exchanges/{id}/export` | Retrieve and normalize selected revision to local manifest/blobs |

The helper must verify its local caller via a random per-launch secret and validate parameters. Return structured error codes (`AUTH_REQUIRED`, `FORBIDDEN`, `NOT_FOUND`, `SDK_UNAVAILABLE`, `UNSUPPORTED_GEOMETRY`, `NETWORK_ERROR`, `INTERNAL_ERROR`). Do not log credentials, access tokens, or sensitive model payloads by default.

For the earliest spike, implement `health` and a **single configured exchange ID** first; only build full project browsing after SDK feasibility is proven.

## 8. Authentication and security

- Each user authenticates independently with their own Autodesk account.
- Use an officially supported delegated authorization flow; prefer system browser + PKCE where supported. Verify exact SDK/APS integration.
- Do not ship a client secret, hard-coded user token, or service account token.
- Protect refresh tokens using Windows credential facilities or an SDK-supported secure cache; avoid plaintext files.
- Restrict helper listening to loopback and authenticate every non-health request; consider protecting health too if it exposes details.
- Clean up temporary geometry files, with restrictive permissions and no world-readable directories.
- Access to an exchange is determined by Autodesk cloud permissions, not by possession of the add-in.
- For a prototype, support one active Autodesk user per local helper instance and make account switching explicit.

## 9. Refresh semantics

**MVP refresh is full replacement**, not per-element delta synchronization.

Algorithm:

1. Read the exchange ID and imported revision ID from the active Fusion document.
2. Ask helper for latest accessible revision.
3. If same, return without changes.
4. Retrieve normalized geometry and validate manifest/blobs before mutating Fusion.
5. Create a new temporary imported group in Fusion.
6. If all required geometry creation succeeds, swap it into place, remove old imported group, and update revision metadata.
7. If import fails, discard temporary group and preserve previous reference geometry and revision metadata.

If Fusion's API cannot support an atomic swap, use an explicitly recoverable staged replacement with tests for interruption. Avoid user-created geometry changes. Treat locally modified imported content as disposable reference data and warn before replacement where necessary.

## 10. Repository layout

```text
fusion-data-exchange-mvp/
  README.md
  docs/
    feasibility.md
    architecture.md
    install-windows.md
    test-plan.md
    known-limitations.md
  fusion_addin/
    FusionDataExchange/        # Fusion-recognized add-in folder
      ...                      # manifest + Python entrypoint per current Fusion conventions
    src/
      ui/
      helper_client/
      geometry_import/
      document_metadata/
  helper/
    FusionDxHelper.sln
    src/
      FusionDxHelper.Api/
      FusionDxHelper.Core/
      FusionDxHelper.AutodeskSdk/
    tests/
  contracts/
    manifest.schema.json
    errors.schema.json
    samples/
  tests/
    fixtures/
    integration/
  scripts/
    build.ps1
    package.ps1
    smoke-test.ps1
```

The coding agent should adjust the add-in packaging to the verified Fusion conventions, while preserving separation between Fusion UI, IPC, SDK adapter, and geometry conversion.

## 11. Delivery milestones

### Milestone 0 — SDK feasibility spike

- Confirm SDK package/runtime/auth and access to a test Revit exchange.
- Build a standalone .NET console reader that prints exchange ID, revision, element count, geometry types, units, and a small property sample.
- Write `docs/feasibility.md` with citations to SDK docs and actual test output.
- **Gate:** No Fusion UI work until real exchange geometry can be read.

### Milestone 1 — Local helper and fixture pipeline

- Implement helper health, auth, revision lookup, and normalized export.
- Define JSON schema and sample fixture independent of the Autodesk SDK.
- Add unit tests for normalization, error handling, and transforms.
- **Gate:** A known exchange produces a valid manifest and geometry artifacts.

### Milestone 2 — Fusion import

- Build Fusion add-in command and helper IPC client.
- Import fixture geometry first, then real exchange geometry.
- Persist reference metadata in the Fusion document.
- **Gate:** A real Revit exchange is visible at correct scale/orientation in Fusion.

### Milestone 3 — Refresh and robustness

- Implement latest-revision check and full replacement.
- Add progress reporting, cancellation where feasible, diagnostics, and rollback.
- **Gate:** Revision N → N+1 updates only the imported reference group; unchanged revision is a no-op.

### Milestone 4 — Two-user acceptance

- Package add-in + helper for two Windows machines.
- Sign in with two separate authorized Autodesk accounts.
- Import the same exchange into two separate Fusion documents.
- Publish a new revision **from Revit using the existing connector**, then refresh both Fusion documents.
- **Gate:** Both users independently display the same latest exchange revision and matching test geometry, without sharing local files or credentials.

## 12. Acceptance tests

| ID | Scenario | Pass condition |
|---|---|---|
| AT-01 | User A signs in | Own Autodesk identity used; no embedded secrets |
| AT-02 | User B signs in | Different Autodesk account works independently |
| AT-03 | Same exchange | Both users resolve the same exchange ID and revision |
| AT-04 | Import geometry | Both see expected test elements at correct scale and placement |
| AT-05 | Save/reopen | Exchange ID, revision, and imported root can be recovered |
| AT-06 | No-op refresh | Same revision does not duplicate geometry |
| AT-07 | New revision | Both users can manually refresh to revision N+1 |
| AT-08 | Preserve user work | Refresh changes only connector-managed reference geometry |
| AT-09 | Access denied | Unauthorized user receives actionable error, not model data |
| AT-10 | Offline | Existing reference remains intact; refresh reports network failure |
| AT-11 | Partial/invalid geometry | Error reported and prior revision preserved or explicitly recoverable |
| AT-12 | Independent installation | User B installs from packaged artifacts without copying User A's credentials/cache |

### 12.1 Test fixture

Create or obtain a small, non-sensitive Revit-authored exchange with:
- One wall, one floor, one column, one beam, and at least two repeated instances.
- A known physical dimension for scale checking.
- A nonzero translation and rotation to test transforms.
- One useful property per element, if provided by the exchange.
- Revision N+1 with one geometry change and one element addition or deletion.

Record the expected object count, bounding box, dimensions, and revision IDs. Compare actual Fusion results against documented tolerances selected after verifying geometry representation and API precision.

## 13. Nonfunctional requirements

- **Reliability:** Failed refresh must not corrupt the user's preexisting design or silently delete reference geometry.
- **Observability:** Local log includes correlation ID, helper version, SDK version, exchange ID, revision ID, counts, duration, and error codes; redact tokens and model data.
- **Performance:** Instrument time and memory for the fixture and a moderately sized exchange. Do not promise production-scale performance in the MVP.
- **Reproducibility:** Pinned SDK/runtime versions, build instructions, fixture-based tests, and deterministic packaging.
- **Compatibility:** Explicitly state tested Fusion build, Windows version, SDK version, and .NET runtime.
- **No backend:** No custom internet-hosted service, database, or webhook infrastructure in MVP.

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| SDK access, license, or API mismatch | Milestone 0 validation before UI development |
| Exchange cannot be read with available scopes | Verify delegated auth and project membership with two test users |
| B-rep conversion unsupported | Mesh/reference geometry first; document fidelity limitations |
| Fusion coordinate/origin constraints | Deterministic transform strategy and fixture-based checks |
| Large model memory use | Batching, chunked blobs, progress/cancellation, measured limits |
| SDK helper Windows-only | Restrict MVP to Windows; evaluate macOS separately |
| Imported entities cannot be truly locked | Dedicated labeled reference group and documented behavior |
| Refresh deletes user edits | Stage replacement; isolate imported content; rollback on failure |
| Project/exchange browsing unavailable | Start with explicit exchange ID/URL input |
| Source IDs change between revisions | Full-replacement refresh avoids requiring stable per-element IDs in MVP |

## 15. Coding-agent operating instructions

1. **Read and verify current Autodesk documentation first.** Record actual SDK version, APIs, and dependencies in `docs/feasibility.md`; do not hallucinate method names or claim untested integrations work.
2. **Implement vertical slices.** Start with SDK console read, then normalized manifest, fixture import, real import, refresh, and two-user test.
3. **Use adapters and mocks.** Separate Autodesk SDK calls behind an interface; keep Fusion API calls behind an import interface so unit tests can run without Fusion.
4. **Do not invent credentials or access.** Use documented environment configuration and interactive user sign-in; provide `.env.example` or equivalent with placeholders only.
5. **Never commit secrets, access tokens, real customer model data, or proprietary SDK binaries** unless redistribution is expressly permitted.
6. **Use test-first verification** for transforms, units, schema validation, and refresh metadata; provide clear manual test instructions for Fusion UI flows.
7. **Stop at blockers with evidence.** If access to the real SDK, Autodesk accounts, or sample exchange is unavailable, complete mock/fixture-based implementation, label it as such, and identify exact steps needed to unblock live integration.
8. **Document all unsupported behavior** rather than quietly substituting local files for a real cloud exchange.
9. **Keep scope read-only.** Do not add Fusion-to-Data Exchange publishing or write-back to Revit in this MVP.
10. **Report completion against acceptance tests**, distinguishing automated pass, manual pass, blocked, and not tested.

## 16. Definition of done

The MVP is done only when two distinct authorized users on separate Windows computers can install the add-in, sign in independently, load the same Revit-authored Data Exchange from a shared cloud project into separate Fusion documents, save/reopen those documents, and manually refresh to a newly published exchange revision without damaging unrelated Fusion geometry. Provide install steps, a short demo script, logs, known limitations, and test evidence.

If live cloud access is unavailable, a fixture-only prototype is a **partial milestone**, not a completed MVP.

## 17. Suggested first coding-agent task

> Build Milestone 0 and the repository skeleton. Verify the current Autodesk Data Exchange .NET SDK's actual APIs, supported Windows runtime, delegated authentication, and Revit exchange geometry retrieval. Implement a minimal console spike that can read one real exchange and print metadata/geometry summary. Create `docs/feasibility.md` with sources, commands, observed results, blockers, and the proposed adapter interface. Do not begin the Fusion UI or claim cloud integration works until the console spike succeeds. If Autodesk access is missing, implement a mock reader and fixture schema, explicitly label live integration blocked, and list the exact prerequisites needed.

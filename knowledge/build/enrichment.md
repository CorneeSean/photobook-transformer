---
type: Playbook
title: Immich enrichment — build order (steps 17+)
description: Adopt Immich API surface that didn't exist when steps 4–5 were built — faces/people and lead-face prioritization first, then rating/OCR/geocoding — to add curation signals without new local ML dependencies.
tags: [playbook, build-order, immich, faces, people, lead-faces, select, caption, enrichment]
timestamp: 2026-07-14
---

# Immich enrichment — build order (steps 17+)

Follow-on to the [build order](/build/build-order.md) (which ended at step 16). The
Immich REST API has grown since the [client](/stages/sync.md) was pinned at steps
4–5 (OpenAPI **v3.0.2**; Immich is now ~v1.126+), exposing signals the pipeline can
now consume **without any local ML dependency**.

**Status: steps 17, 17b, 18, 19, 20 all done and merged** (OCR deferred — see below).

| New Immich surface | Endpoint | What it buys us |
|---|---|---|
| **Face detection + people** | `GET /faces?id={assetId}`, `GET /people` | Portrait signal, per-person coverage in `select`, name grounding in `caption`. |
| **Lead-face matching** | `GET /faces?id={referenceAssetId}` | Point at a reference photo of key subjects; `select` prioritizes them — see [Lead faces](#lead-faces-priority-subjects). |
| **Manual star rating** | `rating` field (−1…5) | Honor the user's own curation as a `select` signal. |
| **OCR text** | `GET /assets/{id}/ocr` | Minor caption/filter signal (signs, menus). **Deferred.** |
| **Reverse-geocoded place** | `city`/`state`/`country` on `exifInfo` | Place names for `caption` where GPS exists, no geocoder of ours. |

**Still NOT exposed** (unchanged): CLIP embeddings and any asset-to-asset similarity
endpoint — smart search takes only *text* and returns *assets*, the vector never
crosses the wire. So `quality`'s `open_clip` and the `pgvector` behind
`select`/`cluster` stay necessary ([Immich API notes](/reference/immich-api.md) Q1).
This effort adds signals; it removes no existing code.

## Design notes

- **Faces at the boundary.** `get_faces` is one Pydantic model + one method on the
  [client](/reference/immich-api.md), mirroring `get_duplicates`. The real
  `AssetFaceResponseDto` carries bounding boxes + a nested `person`; flatten to just
  `person.id`/`person.name` + `sourceType`. `person: null` = a detected-but-unassigned
  face (counts toward `face_count`, no `person_id`).
- **N+1 cost, accepted.** `sync` already loops every asset; faces add one call per
  asset. Accepted — `sync` is a one-per-album batch step behind Prefect retries.
  Revisit only if a live run shows it dominating.
- **`select` face rules are additive + genericity-safe** — an album with zero
  recognized faces behaves byte-for-byte as before: (1) *coverage* — admit a missing
  person's best shot; (2) *soft boost* — prefer more-recognized-people shots only on
  near-tie.

### Lead faces (priority subjects)

The user supplies a **reference picture of the key people** (the couple, the baby);
`select` recognizes them across the album and prioritizes their photos.

- **Constraint:** Immich exposes no face-embedding vectors (like CLIP — [Q1](/reference/immich-api.md)),
  so matching a reference *ourselves* would mean adding a face-recognition model —
  exactly the reimplementation this project delegates to Immich. **Don't.**
- **Instead, let Immich's recognition match.** Immich already clusters library faces
  into `Person` entities. The reference picture just *names which people are leads*:
  `reference picture → GET /faces?id={reference_asset_id} → [person_id, …]`.
- **Three `album.yml` inputs:** `reference_image` (local photo — PRIMARY; uploaded via
  `POST /assets`, then poll until Immich's async face-detection job runs, then read
  faces), `reference_asset_id` (asset already in Immich, no upload), or `person_ids`
  (name the people directly). Upload is idempotent (dedupe on Immich sha1 / cache the
  resolved `reference_asset_id`); the resolver tolerates the async gap and errors
  clearly if detection never completes. A lead face *unassigned* in Immich is
  surfaced as a warning (Immich needs it confirmed once before cross-album matching).
- **How `select` prioritizes** (boost, not filter — a book of *only* leads is worse
  than one that leads with them): guaranteed per-scene coverage (a lead is never
  culled as a near-dup of a non-lead), a ranking boost over the generic tie-break,
  and graceful opt-in (no `lead_faces` ⇒ generic people-aware behavior). Boost
  strength is a config tunable, like `select`'s similarity `threshold`.

## Schema

Structured, reason-about-able columns (per the [data model](/reference/data-model.md)
philosophy, not an opaque dict): two `Photo` columns (`face_count`, `person_ids`) plus
an album-scoped `Person` cache (id→name for `caption`; `is_lead` marks priority
subjects). All nullable for resumability. **Canonical schema:
[data model](/reference/data-model.md).**

## The steps (all done)

Each followed the [implementation workflow](/process/implementation-workflow.md).

- **17. Faces in client + `sync` (+ schema).** `ImmichFace` + `get_faces` (`?id=`
  contract); migration for `Photo.face_count`/`person_ids` + `people` table; `sync`
  fetches faces per asset, upserts `Person` idempotently (keyed on
  `immich_person_id`), failure-isolated (a failing face call leaves columns `NULL`,
  never aborts the album). Genericity guard: zero-face asset ⇒ `face_count=0`,
  `person_ids=[]`.
- **17b. Lead-face resolution.** `lead_faces` in `album.yml` resolved to lead person
  IDs (`Person.is_lead=True`); client `upload_asset` (`POST /assets`); resolver
  uploads once (idempotent), waits out async detection (retry/backoff + timeout),
  reads faces; `reference_asset_id`/`person_ids` skip upload; unassigned face → warning.
- **18. People-aware + lead-aware `select`.** Coverage admits a missing person's best
  shot; near-tie prefers group shots; leads get guaranteed per-scene coverage and
  outrank the tie-break, never dropped as a near-dup of a non-lead; boost is
  config-tunable. Genericity guard asserted against the step-16 wedding + a
  segment-less fixture.
- **19. Name-grounded `caption` (optional).** A photo's `person_ids` → `Person.name`s
  passed into the grounding context of the **one** shared analysis call (no 2nd
  round-trip); unnamed/no-people photos caption cleanly (no "None" leaking).
- **20. Rating + geocoded place (optional).** `sync` reads `rating` +
  `exifInfo.city/state/country` off data it already fetches (**no extra call**);
  `rating` → soft per-star `select` boost; place → `caption` `Location:` line.
  `cluster` deliberately **unchanged** (keeps GPS/DBSCAN — clustering on place names
  was judged speculative and a risk to well-tested scene detection).

**OCR deferred**, on four counts: (1) not free — a *second* per-asset N+1
(`GET /assets/{id}/ocr`); (2) the playbook rates it the weakest signal; (3) its
contract isn't in the pinned v3.0.2 snapshot, so it'd be modeled blind; (4)
consuming it well is a real design problem — raw menu/sign fragments can *worsen*
captions. Revisit only when a concrete album shows it would help; the columns can be
added the same cost-free way as rating/place if the data moves onto the asset DTO.

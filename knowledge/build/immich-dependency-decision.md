---
type: Decision Record
title: Immich dependency — keep or replace with custom code
description: Whether to drop the self-hosted Immich stack and reimplement its two roles (managed photo library + face/dedup ML) in-process with open-source models. Decision deferred; the boundary is kept replaceable.
tags: [decision, immich, dependency, insightface, faces, dedupe, infrastructure, boundary]
timestamp: 2026-07-16
---

# Immich dependency — keep or replace with custom code

**Status:** Open — analysis recorded, no switch made. Immich stays for now; the
boundary is kept clean so this decision can be revisited cheaply.

**Question.** Immich is a whole second stack (its own Postgres, Redis/Valkey,
server, and ML container — see [`docker-compose.immich.yml`](../../docker-compose.immich.yml)).
Can we drop it and reimplement what we actually use with our own code + open-source
models, without losing pipeline capability?

This is the *inverse* of [open question #6](/build/open-questions.md), which asked whether we
could offload *our* local ML **to** Immich and was resolved "no, keep local ML."
That answer stands and is a load-bearing input here: because we already run
`open_clip` + the LAION aesthetic head ourselves, Immich is **not** carrying the
pipeline's hard ML. This record asks the opposite — whether Immich is carrying
*enough of the rest* to justify its operational weight.

## What we actually consume from Immich

Everything Immich-related is behind a deliberately narrow boundary: the only
module that talks HTTP to Immich is [`client.py`](../../src/album/immich/client.py), and the only
stage that reads from it is [`sync`](/stages/sync.md) — *"the boundary."* Everything
downstream reads our Postgres DB, never Immich. Immich fills **two roles** across
**seven capabilities**:

| # | Capability | Endpoint | Role |
|---|---|---|---|
| 1 | Photo **library / storage** — albums as the unit of work (`immich_album_id`) | `POST /search/metadata` (paginated) | Storage |
| 2 | **EXIF** — capture time, IANA timezone, GPS, camera make/model | `exifInfo` on assets | Storage |
| 3 | **Thumbnail** serving — downscaled JPEGs for AI analysis | `GET /assets/{id}/thumbnail` | Storage |
| 4 | **Original** serving — full-res pixels for [`render`](/stages/render.md) (darktable) | `GET /assets/{id}/original` | Storage |
| 5 | **Upload + content-dedup** — for `lead_faces.reference_image` | `POST /assets` | Storage |
| 6 | **Duplicate detection** — ML-derived perceptual dup groups | `GET /duplicates`, `duplicateId` | **ML** |
| 7 | **Faces + people/recognition** — clusters library faces into named `Person`s | `GET /faces?id=`, `GET /people` | **ML** |

The pipeline **already computes the ML Immich does *not* expose** ([Immich API
notes](/reference/immich-api.md)): CLIP embeddings ([`quality`](/stages/quality.md) → `open_clip`
ViT-L/14), the LAION aesthetic score, and OpenCV blur. So the *only* ML we rent
from Immich is **duplicate detection (6)** and **face recognition (7)**. Roles 1–5
are plumbing.

## Replaceability, component by component

The key fact: **Immich's ML is not proprietary.** Immich = InsightFace (faces) +
OpenCLIP (search) + Postgres/pgvector + exiftool, wrapped as a self-hosted app. We
already run OpenCLIP and pgvector ourselves.

| Immich capability | Custom replacement | Difficulty |
|---|---|---|
| Library / assets (1) | Filesystem scan of a `photos/` dir → our existing `Photo` rows; `immich_album_id` becomes a directory path | Trivial |
| EXIF (2) | `exiftool` / `Pillow` / `exifread` (Immich itself just shells to exiftool); the UTC/timezone logic in [`sync`](/stages/sync.md) is already ours | Trivial |
| Thumbnails (3) | `pyvips` / `Pillow`, cached to disk | Trivial |
| Originals (4) | Read the file | Trivial |
| Upload/dedup-on-import (5) | sha1 the file (only used for the lead-face reference image) | Trivial |
| Duplicate detection (6) | `imagededup`/`imagehash` (pHash/dHash) — **or reuse the CLIP embeddings + pgvector cosine we already store** for `select`/`cluster`, deriving dup groups from the same vectors and deleting the need entirely | Easy |
| **Faces + recognition + people (7)** | **InsightFace** (`buffalo_l`: RetinaFace detector + ResNet50/ArcFace embeddings) + DBSCAN/HDBSCAN over the embeddings to reconstruct the `Person` concept | Moderate — the only real work |

`buffalo_l` is, in fact, **the same model family Immich runs under the hood.**
Replacing Immich's faces means adopting InsightFace directly rather than through
Immich's API.

### The one legal footgun: InsightFace model licensing

InsightFace's *code* is MIT, but its **pretrained packs (incl. `buffalo_l`) are
licensed non-commercial / research-only.** Immich shields us because it's a
separately-distributed self-hosted app; vendoring InsightFace into our own product
for commercial use would inherit that constraint (train/buy a recognition model
instead). For a personal photobook this is almost certainly fine — but it must be a
conscious call. Cleaner-licensed but weaker alternatives: `face_recognition`/dlib
(older, lower accuracy), DeepFace (wraps various backends).

## Assessment

1. **The boundary is already built for this.** Replacing Immich touches *one class
   and one stage* ([`client.py`](../../src/album/immich/client.py) + [`sync`](/stages/sync.md)). Nothing
   downstream changes — `select`, `cluster`, `caption`, `render` all read the DB.
   This is the payoff of the [architecture](/foundations/architecture.md) firewall.

2. **We already pay for Immich but use little of it.** We run the full Immich stack
   yet consume only dup-detection + faces from its ML — both reproducible in a few
   hundred lines of InsightFace + our existing pgvector. The hard ML (CLIP,
   aesthetics) is already ours per [open question #6](/build/open-questions.md).

3. **What Immich actually earns is the *library*, not the ML.** Immich is a managed
   library: import pipelines, and a **web UI for the human-in-the-loop** steps the
   design leans on — confirming a `Person` in the UI so cross-album matching works
   ([enrichment](/build/enrichment.md) step 17b), and picking `immich_album_id` from the UI.
   If a human curating in a real app is part of the product, Immich earns its keep.
   If this stays a headless batch job over a folder, Immich is heavy for what it does.

## Decision

**Keep Immich for now; do not reimplement yet. Preserve replaceability.**

The switch is not worth making blind, but the pipeline should never *harden* around
Immich. Concretely:

- Keep the `AssetSource` / `ThumbnailSource` protocols ([`sync`](/stages/sync.md),
  [`quality`](/stages/quality.md)) as the contract. They are what makes a local backend a
  drop-in.
- When the cost of the second stack (or the loss of a UI-less deployment) actually
  bites, build a **`LocalPhotoSource`** satisfying those same protocols —
  filesystem + exiftool + `pyvips` thumbnails + InsightFace faces + pgvector-derived
  dup groups — and run it *alongside* Immich behind a config switch. Then measure
  face-recognition quality and whether losing the browse/curate UI is acceptable
  before deleting anything.

The genuinely new code is **InsightFace integration + a face-clustering step** (to
reproduce Immich's `Person`). Everything else is plumbing the boundary already
isolates.

## Build steps — `LocalPhotoSource` (gated on this decision flipping to "replace")

These steps are **not scheduled**. They belong to the mainline
[build order](/build/build-order.md) only *if and when* the decision above flips to "replace"
(see [Revisit when](#revisit-when)). They are recorded now so the path is concrete
and the [boundary](/stages/sync.md) is kept honest against it. Numbered `L1…L5` to signal
they are conditional, not part of the mainline 1–22+ sequence.

Each follows the [implementation workflow](/process/implementation-workflow.md) (branch, TDD,
sub-agent review, `uv run poe check` green, squash, fast-forward merge) and the
[quality practices](/process/quality-practices.md) (injected dependencies, protocol-shaped
seams). The north star: **the switch is a config choice, and no downstream stage
changes** — everything continues to read the DB behind the `AssetSource` /
`ThumbnailSource` protocols.

**L1. Filesystem library behind the existing protocols.** A `LocalPhotoSource`
that satisfies the same `AssetSource` (`get_album_assets`) and `ThumbnailSource`
(`get_thumbnail`) protocols the stages already depend on, plus a `get_original`,
reading a `photos/<album>/` directory instead of Immich. EXIF via `exiftool`/`Pillow`
(capture time, IANA timezone, GPS, camera make/model — the exact `ImmichExif`
subset [`sync`](/stages/sync.md) consumes, so its UTC-normalization logic is untouched);
thumbnails via `pyvips`, cached to disk. `immich_album_id` generalizes to a source
handle (path). *Test first*, against a small fixture tree: assets enumerate with
correct EXIF; a missing-timezone photo still lands the same "ambiguous → NULL
timestamp" path as today; thumbnails decode. **No downstream change; `sync` runs
unmodified against the fake.** **Commit.**

**L2. Duplicate groups without Immich.** Reproduce role 6 (`get_duplicates` /
`duplicate_group_id`). Prefer **reusing the CLIP embeddings + `pgvector` cosine we
already compute** ([`quality`](/stages/quality.md)) — derive dup groups by thresholded
nearest-neighbour over the same vectors, deleting the need for a separate dedup
model entirely; fall back to `imagehash` (pHash/dHash) only if a pre-embedding
signal is wanted. *Test first*: near-identical frames group together; unrelated
photos don't; the grouping populates `Photo.duplicate_group_id` in the shape
[`dedupe`](/stages/dedupe.md) already expects. **Commit.**

**L3. InsightFace faces + the `Person` concept.** Reproduce role 7 — the one piece
of real ML. Detect + embed faces with **InsightFace `buffalo_l`** (RetinaFace +
ArcFace), then **cluster the embeddings (DBSCAN/HDBSCAN) into album-scoped
`Person`s**, populating `Photo.face_count` / `Photo.person_ids` and upserting
`Person` rows exactly as [`sync`](/stages/sync.md)'s `_sync_faces` does today. Keep it
**failure-isolated per asset** (one undetectable photo leaves face columns NULL,
never aborts the album) and **idempotent** (re-run reuses stable cluster ids).
Record the [InsightFace non-commercial model licence](#the-one-legal-footgun-insightface-model-licensing)
as a conscious acceptance in [`log.md`](/log.md). *Test first*, against fixture faces:
two photos of the same person share a `person_id`; a face-free album lands
`person_ids=[]` and every people-aware stage reduces to its face-free behaviour
(genericity guard); re-running doesn't duplicate `Person` rows. **Commit.**

**L4. Lead faces without upload.** Rework `lead_faces` resolution
([enrichment](/build/enrichment.md) 17b) for the local backend: the `reference_image`
is embedded with the same InsightFace model and matched (cosine) against the
album's clustered `Person`s — **no `POST /assets` upload, no async
face-detection poll**, which the local path removes entirely. `reference_asset_id`
generalizes to "a photo already in the library"; `person_ids` still passes through.
*Test first*: a reference photo of a lead resolves to the right `Person` and marks
it `is_lead=True`; a reference face matching nobody surfaces a clear warning; no
`lead_faces` ⇒ no leads, no error. **Commit.**

**L5. Config switch + end-to-end.** Wire a `source: immich | local` setting so
`flow.py` injects `ImmichClient` or `LocalPhotoSource` (both satisfy the
protocols) — **the only line the switch touches**. Add an integration test that
runs the pipeline end-to-end over a local fixture album with no Immich stack
running, and A/B the face-recognition + curation output against an Immich run on
the same photos before deleting anything. Update [tech stack](/reference/tech-stack.md),
[architecture](/foundations/architecture.md), [sync](/stages/sync.md), and this record's status.
Only after this A/B is acceptable does dropping [`docker-compose.immich.yml`](../../docker-compose.immich.yml)
become a separate cleanup step. **Commit.**

## Revisit when

- The Immich stack's operational weight (RAM, containers, upgrade churn) becomes a
  real cost, **or**
- We need a headless / no-UI deployment, **or**
- Immich's face-recognition quality or API stability disappoints on a live run
  (the [Immich API notes](/reference/immich-api.md) are still contract-derived, not yet
  verified against a running instance).

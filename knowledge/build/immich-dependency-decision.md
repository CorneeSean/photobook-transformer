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
| 5 | **Upload + content-dedup** — for `lead_faces.reference_images` | `POST /assets` | Storage |
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
| Upload/dedup-on-import (5) | sha1 the file (only used for lead-face reference images) | Trivial |
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

**Not scheduled** — recorded so the path is concrete and the [boundary](/stages/sync.md) stays
honest. Only enter the mainline [build order](/build/build-order.md) *if* the decision flips
(see [Revisit when](#revisit-when)). North star: **the switch is a config choice; no
downstream stage changes** (everything reads the DB behind the `AssetSource` /
`ThumbnailSource` protocols). Each follows the usual
[implementation workflow](/process/implementation-workflow.md).

- **L1. Filesystem library behind the existing protocols.** `LocalPhotoSource` satisfies
  `AssetSource` + `ThumbnailSource` (+ `get_original`) reading a `photos/<album>/` dir:
  EXIF via `exiftool`/`Pillow`, thumbnails via `pyvips`; `immich_album_id` → a path. `sync`
  runs unmodified against it.
- **L2. Duplicate groups.** Prefer deriving dup groups from the **CLIP embeddings +
  `pgvector` cosine we already compute** (thresholded NN), deleting the need for a separate
  dedup model; `imagehash` fallback. Populates `Photo.duplicate_group_id`.
- **L3. InsightFace faces + `Person`** (the one real ML piece). Detect+embed with
  `buffalo_l` (RetinaFace + ArcFace), cluster embeddings (DBSCAN) into album-scoped
  `Person`s exactly as `sync._sync_faces` does — failure-isolated + idempotent. Record the
  [InsightFace non-commercial licence](#the-one-legal-footgun-insightface-model-licensing)
  as conscious acceptance.
- **L4. Lead faces without upload.** Embed `reference_images` with the same model, match by
  cosine against clustered `Person`s — no `POST /assets`, no async poll.
- **L5. Config switch + end-to-end.** A `source: immich | local` setting picks which the
  flow injects (the only line it touches); A/B recognition + curation against an Immich run
  before deleting `docker-compose.immich.yml`.

## Revisit when

- The Immich stack's operational weight (RAM, containers, upgrade churn) becomes a
  real cost, **or**
- We need a headless / no-UI deployment, **or**
- Immich's face-recognition quality or API stability disappoints on a live run
  (the [Immich API notes](/reference/immich-api.md) are still contract-derived, not yet
  verified against a running instance).

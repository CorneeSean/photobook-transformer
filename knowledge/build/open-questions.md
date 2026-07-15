---
type: Open Questions
title: Open questions
description: Unresolved questions to settle early; record answers back into this bundle.
tags: [open-questions, risks, decisions]
timestamp: 2026-07-12
---

# Open questions

Resolve early; record answers back into this bundle (and note them in
[`log.md`](/log.md)).

1. ~~**Does Immich's API expose CLIP embeddings**, or only duplicate groups and
   search results?~~ **RESOLVED (step 4, 2026-07-13): No.** The public REST API
   (OpenAPI v3.0.2) exposes no embeddings — only duplicate groups
   (`GET /duplicates`, `AssetResponseDto.duplicateId`) and smart-search *results*.
   So [`quality`](/stages/quality.md) computes the `clip_embedding` itself via
   `open_clip`. See [Immich API notes](/reference/immich-api.md).

2. **Confirm the honeymoon photos genuinely lack GPS** — check a few originals
   never shared through messaging apps (which strip EXIF). If some have it, the
   spatial path in `cluster` gets exercised on the very first album.

3. **Per-photo source timezone.** The schema decision is settled — everything is
   stored tz-aware in UTC, `album.yml` carries offsets (or an album `timezone`),
   and `cluster` matches in UTC (see the [data-model notes](/reference/data-model.md)).
   What remains open is narrower: **how reliably can we determine each photo's
   capture zone?** Check whether Immich surfaces the EXIF offset
   (`OffsetTimeOriginal`) or only a bare timestamp; if only the latter, the album
   `timezone` is the fallback, which is wrong for an album that itself spans zones
   mid-segment. Confirm in [build step 5](/build/build-order.md) against real honeymoon
   assets. *Partial answer (step 4): Immich **does** expose `exifInfo.timeZone`
   (IANA) + `dateTimeOriginal`, which [`sync`](/stages/sync.md) now uses before
   falling back to `Album.timezone` — see [Immich API notes](/reference/immich-api.md). The
   mid-segment zone-span edge and these fields' reliability on real assets still
   need a live check.*

4. ~~**Lulu's trim size / bleed / spine-width spec**~~ **RESOLVED (step 13,
   2026-07-14):** Per Lulu's Print API docs (Book Creation Guide, PDF Creation
   Settings, and the "How is spine width calculated?" article), we pin
   [`assemble`](/stages/assemble.md) to Lulu's **Square 8.5×8.5in** trim —
   a standard photo-book size in their catalog. Bleed is a flat **0.125in on
   every edge** (so the rendered PDF page is 8.75×8.75in — trim + bleed on
   each side), with a minimum 0.25in safety margin for interior content (0.5in
   used here for headroom) and a 0.2in inner gutter margin. Spine width for
   softcover perfect binding is `(page_count / 444) + 0.06in`, where **444 is
   Lulu's PPI (pages-per-inch) constant for their standard interior paper
   stock** — i.e. spine width scales linearly with page count divided by how
   many of that stock's sheets pack into an inch, plus a fixed 0.06in cover
   material offset. (Lulu also documents 460 PPI for magazine/comic stock and
   a page-count lookup table for hardcover casewrap/linen — not used here
   since we target the standard softcover perfect-bound product.) These
   constants are now the source for `assemble`'s template dimensions —
   see `src/album/stages/assemble.py`.

5. **What does the full AI pass cost?** Estimate the Claude API spend for
   ~300–500 selected photos before running it, and confirm it's acceptable. (Note
   the [single combined vision call](/foundations/architecture.md) halves the number of image
   uploads versus separate enhance/caption calls.)

6. ~~**Can we offload the local ML (`open_clip` + LAION aesthetic head) to
   Immich — reuse its embeddings, or read them from its DB — and drop the
   dependency?**~~ **RESOLVED (2026-07-14, user-directed): No — keep the local ML
   pipeline.** The deciding fact: Immich computes **no aesthetic/quality score**,
   and its CLIP embeddings default to a **SigLIP** model whose vector space is
   *incompatible* with the LAION aesthetic predictor (trained on **OpenAI CLIP
   ViT-L/14**, 768-dim — SigLIP's sigmoid/WebLI space ≠ OpenAI's softmax/LAION
   space, even at the same dimension). So aesthetic scoring forces us to run
   ViT-L/14 (`open_clip`) ourselves regardless — and once it's running, the
   `cluster`/`select` embeddings come free from the same forward pass, leaving
   nothing for Immich to offload. Reading embeddings from **Immich's Postgres was
   rejected**: its internal schema is an unversioned, churning contract (vector
   extension migrated pgvecto.rs → pgvector/VectorChord; `smart_search.embedding`
   width changes with the configured model; face embeddings moved
   `asset_faces` → `face_search` across releases), which violates the
   [architecture](/foundations/architecture.md) firewall against touching Immich's schema —
   too fragile a coupling to save a forward pass we're keeping anyway. A
   local-ML-free "minimal" variant is *possible* (rank `select` on Immich star
   `rating` + faces + duplicate groups, temporal-only `cluster`) but trades away
   the automated best-of-scene curation that is the pipeline's core value — not
   taken. The faces/rating/OCR enrichment ([Immich enrichment](/build/enrichment.md))
   is unaffected: it's additive and API-only. See the full research write-up in
   [`log.md`](/log.md) 2026-07-14.

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

4. ~~**Lulu's trim size / bleed / spine-width spec**~~ **RESOLVED (step 13):**
   [`assemble`](/stages/assemble.md) is pinned to Lulu's **Square 8.5×8.5in** trim,
   **0.125in bleed** every edge (→ 8.75in page), 0.5in safety + 0.2in gutter margins,
   spine = `(page_count / 444) + 0.06in` (444 = Lulu's PPI for standard softcover
   stock). These constants drive `assemble`'s template dimensions.

5. ~~**What does the full AI pass cost?**~~ **RESOLVED: acceptable — ~$1–$4/album.**
   One combined vision call per selected photo at a small thumbnail. Full math + levers:
   [AI-pass cost estimate](/build/archive/ai-cost-estimate.md). Metered re-check deferred to
   first live run. (Moot for a default run — offline since step 29.)

6. ~~**Can we offload the local ML to Immich and drop the dependency?**~~ **RESOLVED
   (user-directed): No — keep local ML.** Immich computes no aesthetic score, and its
   default SigLIP embeddings are incompatible with the LAION predictor (needs OpenAI
   CLIP ViT-L/14), so we run `open_clip` regardless — and `cluster`/`select` embeddings
   then come free from the same pass. Reading Immich's Postgres directly was rejected
   (unversioned, churning schema — violates the [architecture](/foundations/architecture.md)
   firewall). Full write-up: [`log.md`](/log.md) 2026-07-14.

7. ~~**The "selected photos, ordered by id" query is repeated in
   `caption`/`enhance`/`render`.**~~ **RESOLVED: hoisted to
   `db/queries.py::selected_photos(album_id, session)`** (a query-functions module, not
   a Repository — SQLAlchemy's Session already is a Unit of Work / Identity Map). Also
   fixed the drift the note flagged (`caption` lacked `.order_by(Photo.id)`).

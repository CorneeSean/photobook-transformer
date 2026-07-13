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
   `open_clip`. See [Immich API notes](/immich-api.md).

2. **Confirm the honeymoon photos genuinely lack GPS** — check a few originals
   never shared through messaging apps (which strip EXIF). If some have it, the
   spatial path in `cluster` gets exercised on the very first album.

3. **Per-photo source timezone.** The schema decision is settled — everything is
   stored tz-aware in UTC, `album.yml` carries offsets (or an album `timezone`),
   and `cluster` matches in UTC (see the [data-model notes](/data-model.md)).
   What remains open is narrower: **how reliably can we determine each photo's
   capture zone?** Check whether Immich surfaces the EXIF offset
   (`OffsetTimeOriginal`) or only a bare timestamp; if only the latter, the album
   `timezone` is the fallback, which is wrong for an album that itself spans zones
   mid-segment. Confirm in [build step 5](/build-order.md) against real honeymoon
   assets. *Partial answer (step 4): Immich **does** expose `exifInfo.timeZone`
   (IANA) + `dateTimeOriginal`, which [`sync`](/stages/sync.md) now uses before
   falling back to `Album.timezone` — see [Immich API notes](/immich-api.md). The
   mid-segment zone-span edge and these fields' reliability on real assets still
   need a live check.*

4. **Lulu's trim size / bleed / spine-width spec** — drives Jinja2 template
   dimensions, so pin it down before building [`assemble`](/stages/assemble.md).

5. **What does the full AI pass cost?** Estimate the Claude API spend for
   ~300–500 selected photos before running it, and confirm it's acceptable. (Note
   the [single combined vision call](/architecture.md) halves the number of image
   uploads versus separate enhance/caption calls.)

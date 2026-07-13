---
type: Reference
title: Immich API notes
description: What the Immich REST API exposes that the pipeline relies on — and the answer to open question #1 (CLIP embeddings).
tags: [reference, immich, api, embeddings, timezone]
timestamp: 2026-07-13
---

# Immich API notes

Findings pinned down while building the [Immich client](/build-order.md) (step 4),
against the Immich **OpenAPI spec v3.0.2** (the project's live Immich version).
The [`client.py`](../src/album/immich/client.py) wrapper is modelled on these.

## Open question #1 — does Immich expose CLIP embeddings? **No.**

The public REST API exposes **no CLIP / smart-search embeddings**. The OpenAPI
spec contains zero `embedding` fields; embeddings live only in Immich's internal
`smart_search` table and are never returned over HTTP. What *is* exposed:

- **Smart search** (`POST /search/smart`) — returns matching *assets* for a text
  query; the vector itself never crosses the wire.
- **Duplicate detection** (`GET /duplicates`, and `AssetResponseDto.duplicateId`
  on each asset) — the ML-derived output we *can* consume.

**Consequence:** the [`quality`](/stages/quality.md) stage must compute the
[`clip_embedding`](/data-model.md) itself via `open_clip` (ViT-L/14, matching the
768-dim column). Confirmed decision — this is no longer an open question.

## Endpoints the pipeline uses

| Need | Endpoint | Notes |
|---|---|---|
| List an album's assets | `POST /search/metadata` `{albumIds, page, size}` | **Not** `GET /albums/{id}` — as of v3.0.2 that no longer embeds an `assets` array. Paginated: response `assets.items` + `assets.nextPage` (string page number, `null` when done). |
| Asset + EXIF | `GET /assets/{id}` | `exifInfo` sub-object; see timezone note below. |
| Duplicate groups | `GET /duplicates` | list of `{duplicateId, assets[], suggestedKeepAssetIds}`. |
| Thumbnail bytes | `GET /assets/{id}/thumbnail?size=thumbnail` | for `quality`/`enhance`. |

Auth is the `x-api-key` header (key from Immich's web UI → Account Settings → API
Keys), base URL includes the `/api` prefix (e.g. `http://localhost:2283/api`).

## Timezone (informs open question #3)

Immich **does** surface per-photo zone info, so [`sync`](/stages/sync.md) needn't
blindly fall back to `Album.timezone`:

- `exifInfo.dateTimeOriginal` — capture time; when it carries an offset it is an
  absolute instant (authoritative).
- `exifInfo.timeZone` — IANA zone name (e.g. `Europe/Podgorica`); occasionally a
  non-IANA string (`UTC+02:00`), which `sync` treats as unknown and falls back.
- `AssetResponseDto.localDateTime` — local wall-clock, used as a fallback capture
  time when `dateTimeOriginal` is absent.

`sync` resolves each photo to UTC as: aware `dateTimeOriginal` → else naive time +
`exifInfo.timeZone` → else `Album.timezone` → else flag ambiguous (timestamp left
NULL). Open question #3's remaining risk — an album that spans zones *mid-segment*
— is covered as far as Immich's data allows; the rest is validated on a live run.

## Caveat

These notes and the client's test fixtures (`tests/fixtures/immich/`) are derived
from the OpenAPI **contract**, not yet from a live capture. Re-verify against a
running instance on the first real `sync` (the live run was intentionally deferred
when steps 4–5 were built without a reachable stack).

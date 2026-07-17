---
type: Reference
title: Immich API notes
description: What the Immich REST API exposes that the pipeline relies on — and the answer to open question #1 (CLIP embeddings).
tags: [reference, immich, api, embeddings, timezone]
timestamp: 2026-07-13
---

# Immich API notes

Findings pinned down while building the [Immich client](/build/build-order.md) (step 4),
against the Immich **OpenAPI spec v3.0.2** (the project's live Immich version at
the time). The [`client.py`](../../src/album/immich/client.py) wrapper is modelled on
these. The **faces/people** surface below was added later (enrichment step 17,
2026-07-14) against a current Immich (~v1.126); the rest of this doc is still the
v3.0.2 snapshot and should be re-verified on the first live run.

## Open question #1 — does Immich expose CLIP embeddings? **No.**

The public REST API exposes **no CLIP / smart-search embeddings**. The OpenAPI
spec contains zero `embedding` fields; embeddings live only in Immich's internal
`smart_search` table and are never returned over HTTP. **Re-confirmed 2026-07-14**
against a current Immich (~v1.126) while adding the faces surface below: still no
embedding fields, and still **no asset-to-asset visual-similarity endpoint**
(smart search only accepts a *text* query and returns *assets*). Face embeddings
are in the same boat — internal only. What *is* exposed:

- **Smart search** (`POST /search/smart`) — returns matching *assets* for a text
  query; the vector itself never crosses the wire.
- **Duplicate detection** (`GET /duplicates`, and `AssetResponseDto.duplicateId`
  on each asset) — the ML-derived output we *can* consume.
- **Faces / people** (`GET /faces?id={assetId}`, `GET /people`) — see below;
  another ML-derived output we consume, no local model needed.

**Consequence:** the [`quality`](/stages/quality.md) stage must compute the
[`clip_embedding`](/reference/data-model.md) itself via `open_clip` (ViT-L/14, matching the
768-dim column). Confirmed decision — this is no longer an open question.

## Endpoints the pipeline uses

| Need | Endpoint | Notes |
|---|---|---|
| List an album's assets | `POST /search/metadata` `{albumIds, page, size}` | **Not** `GET /albums/{id}` — as of v3.0.2 that no longer embeds an `assets` array. Paginated: response `assets.items` + `assets.nextPage` (string page number, `null` when done). |
| Asset + EXIF | `GET /assets/{id}` | `exifInfo` sub-object; see timezone note below. Also carries `rating` (top-level, −1…5 manual stars) and `exifInfo.city`/`state`/`country` (reverse-geocoded place) — both consumed by `sync` from step 20, no extra call. |
| Duplicate groups | `GET /duplicates` | list of `{duplicateId, assets[], suggestedKeepAssetIds}`. |
| Thumbnail bytes | `GET /assets/{id}/thumbnail?size=thumbnail` | for `quality`/`enhance`. |
| Original bytes | `GET /assets/{id}/original` | full-resolution source pixels, for `render` (darktable-cli's input — XMP sidecars carry only edit instructions, not pixels). |
| Faces for an asset | `GET /faces?id={assetId}` | list of `AssetFaceResponseDto`; each has `id`, `sourceType` (`machine-learning`\|`exif`\|`manual`), bounding box, and a nested `person: {id, name, …}` that is **`null` for a detected-but-unassigned face**. `sync` flattens just `person.id`/`person.name` to the lean [`ImmichFace`](../../src/album/immich/client.py). One call *per asset* (an N+1 over the album — accepted; `sync` is a per-album batch behind Prefect retries). |
| Upload an asset | `POST /assets` | multipart form (`assetData` file + `deviceAssetId`/`deviceId`/`fileCreatedAt`/`fileModifiedAt`); returns `{id, status}` where `status` is `created` or `duplicate` (content-dedup — a repeat upload returns the existing asset's id). Used only for `lead_faces.reference_image` (enrichment step 17b). **Face detection runs asynchronously after import**, so the reference's faces appear a short while later — the resolver polls `GET /faces` until they do. |

Auth is the `x-api-key` header (key from Immich's web UI → Account Settings → API
Keys), base URL includes the `/api` prefix (e.g. `http://localhost:2283/api`).

**OCR (`GET /assets/{id}/ocr`) is deliberately not consumed (step 20).** It's the
third enrichment-step-20 signal but was deferred: unlike `rating`/geocoded place
(fields on data `sync` already fetches), OCR is a *separate per-asset endpoint* —
a second N+1 over the album, on top of the faces N+1 — for what the
[enrichment playbook](/build/enrichment.md) itself rates a "minor" signal, and its
contract isn't in this (v3.0.2) snapshot. Revisit if a concrete need appears; see
the enrichment step-20 write-up for the full tradeoff.

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

**Partly closed by the step-24 offline ML stack (2026-07-17):** the faces path
(`upload_asset` → async detection → `get_faces` returning a real
machine-learning face) and CLIP (smart search returning an uploaded asset, i.e.
the embedding ran) are now exercised against a **real** Immich instance — a
pinned, pre-baked, fully-offline stack (see
[testcontainers.md](/build/testcontainers.md) step 24 / `poe test-immich-ml`). Still
contract-only: album pagination on a real library, EXIF/`rating`/geocoded-place on
real synced photos, and duplicate detection — re-verify those on the first live
`sync`.

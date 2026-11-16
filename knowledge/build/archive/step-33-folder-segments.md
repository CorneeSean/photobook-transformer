---
type: Build Playbook
title: "Step 33: Folder-derived segments in ingest.py (photo timestamps → segment windows)"
description: Plan to teach DirectoryIngestor to ingest one album from an AlbumSpec, deriving each segment's [start_at, end_at] window from the capture timestamps of the photos in its subfolder — behind a small segment-source seam shared with the existing PDF path, with empty/single-instant/overlap edge cases handled.
tags: [build, ingest, segments, folders, immich, plan]
timestamp: 2026-07-18
---

# Step 33: Folder-derived segments in `ingest.py`

**Status: todo.** Depends on [step 32](step-32-manifest-schema.md) (imports
`AlbumSpec`/`SegmentSpec`). Second step of the
[folder-structure-driven ingestion playbook](folder-structure-ingestion.md).
The fan-out loop + CLI is [step 34](step-34-fanout-cli.md) — this step delivers
and unit-tests a single-album `ingest_album(spec, root)` in isolation.

## Why

`DirectoryIngestor.ingest` today derives segments only from a `--source-document` PDF
(`_extract_segments`). This step adds the second segment source — a folder's photos —
and factors the two behind one seam so the upload → create → readiness → materialize
→ validate spine is shared, not duplicated. A folder-derived segment's window is
`[min, max]` of the capture timestamps of the photos actually in that subfolder
([playbook detail](folder-structure-ingestion.md#folder-derived-segment-windows--the-load-bearing-detail)).

## Design — a segment-source seam

Today `ingest()` computes `segments` (via `_extract_segments`) **after** upload +
readiness (the PDF path needs the album's capture range from `get_album_assets`). The
folder path also runs after readiness (it needs per-asset capture times). So the only
variant between the two is "how segments are computed"; everything else is shared.

Introduce a small internal seam — a callable that, given the created album + the
`path → asset_id` map built during upload, returns `list[SegmentYAML] | None`:

```python
SegmentSource = Callable[[str, dict[Path, str]], list[SegmentYAML] | None]
#                          immich_album_id, path_to_asset_id  →  segments
```

Refactor the spine into a shared `_ingest(directory, *, slug, title, timezone,
segment_source, lead_face_images)` that keeps the existing-slug guard, uploads (now
**retaining** `path_to_asset_id`), creates the Immich album, waits for readiness,
calls `segment_source(...)`, then materializes + validates. Both public entry points
become thin:

- **`ingest(...)`** (flat / PDF path, unchanged behavior) passes a `segment_source`
  that wraps the existing `_extract_segments` (or `None` when no `--source-document`).
- **`ingest_album(spec: AlbumSpec, root: Path)`** (new) resolves the album directory
  (`root / spec.path`), slug/title/timezone from the spec (falling back to the same
  directory-name defaults), builds a `segment_source` from the spec's `segments`, and
  reuses `_ingest`. `spec.source_document` routes through the existing PDF source
  instead (the manifest loader already guarantees the two are mutually exclusive).

`_collect_images` and the `path_to_asset_id` retention are the only spine changes;
upload becomes `{path: self._client.upload_asset(path) for path in image_paths}`
(dict instead of list). Immich content-dedup means identical files still map cleanly.

## Folder segment building — two pure helpers

Both are pure (no client, no I/O) so they unit-test with plain data:

```python
def _segments_from_folders(
    album_dir: Path,
    specs: list[SegmentSpec],
    path_to_asset_id: dict[Path, str],
    asset_times: dict[str, datetime],
) -> list[SegmentYAML]:
    """One SegmentYAML per spec whose subfolder has dated photos, sorted by start_at.
    A spec whose folder has no images / no dated photos is skipped with a warning."""
```

Per spec: the photos under `album_dir / spec.path` are the `path_to_asset_id` keys
that are relative-to that subfolder; map them to `asset_times` (dropping missing/None),
compute the window via `_folder_time_range`, and build
`SegmentYAML(label=spec.label or _titleize(Path(spec.path).name), place=spec.place,
start_at=..., end_at=...)`. Skip (log a warning) when the folder yields no dated
photo. Finally sort by `start_at` so `Segment.position` is chronological.

```python
def _folder_time_range(times: list[datetime]) -> tuple[datetime, datetime] | None:
    """[min, max] of a folder's capture instants, or None if empty. Widens a
    single-instant window (max == min) to end = min + 1s so the AlbumYAML loader's
    end_at > start_at check passes."""
    if not times:
        return None
    lo, hi = min(times), max(times)
    return (lo, hi if hi > lo else lo + timedelta(seconds=1))
```

`asset_times` comes from a small `_asset_times(assets) -> dict[str, datetime]` over
`get_album_assets(immich_album_id)`, reusing the existing `_asset_time`/`_as_aware`
helpers (EXIF `dateTimeOriginal`, else `localDateTime`; naive coerced to UTC). This is
the same capture-time source `_capture_range` already uses for the PDF grounding hint.

**Overlap** between two folder windows (photos interleave across subfolders) is **not**
handled here — the materialized `album.yml` runs through the existing
`_validate_materialized` (`AlbumDefinition().load`), whose `AlbumYAML` validator raises
on overlap; the resulting `IngestError` names the offending segments. This deliberately
reuses the one source of truth for segment correctness rather than re-checking here.

## Genericity + segments-optional guards

- The new code speaks only album/segment/path — no occasion concept (rule 1).
- A `segments: []` spec, an album folder with no segment subfolders, or every segment
  skipped for want of dated photos all produce an album with **zero** segments — the
  tested downstream fallback (rule 2). `ingest_album` must degrade to exactly today's
  no-segments `album.yml` in that case.

## Tests (TDD — write first, per [working-agreement.md](../../process/working-agreement.md))

Extend `tests/album/test_ingest.py` with the existing fake `IngestClient` (a fake that
returns deterministic asset ids from `upload_asset` and canned `get_album_assets` with
EXIF times), `class TestIngestAlbum:`:

- **Happy path:** a spec with two dated segment subfolders → `album.yml` has two
  segments with `[min, max]` windows, chronologically ordered, labels defaulted from
  folder names; the loader accepts it.
- **Label/place override** from the spec wins over the folder-name default.
- **Empty / undated segment folder** → that segment skipped (warning), album still
  materializes; a spec with *all* segments skipped → a zero-segment `album.yml`
  (byte-compatible with the no-segments flat path).
- **Single-instant folder** (all photos one timestamp) → `end_at == start_at + 1s`,
  loader accepts.
- **Overlapping windows** (interleaved timestamps across two folders) →
  `_validate_materialized` raises `IngestError` naming the segments.
- **`source_document` on the spec** routes through the PDF source (TestModel), not the
  folder source (assert the folder path is not consulted).
- Pure-helper unit tests for `_folder_time_range` (empty → None; single → +1s; normal
  range) and `_segments_from_folders` (skip/sort/label defaulting) with plain data.
- **Existing `ingest()` tests stay green** — the spine refactor must be behavior-
  preserving for the flat/PDF path (the `path_to_asset_id` dict change is internal).

All DB-free/torch-free — default `poe check` collection.

## Docs to update

- [single-command-ingestion.md](single-command-ingestion.md) — cross-reference:
  segments now also come from folders, not only a document.
- [domain-model.md](../../foundations/domain-model.md) — one line noting a Segment's time
  range can be derived from a subfolder's photo timestamps (a new segment *source*, not
  a new concept).
- [progress.md](../progress.md) — move the step-33 row Outstanding → Completed on merge.
- [log.md](../../log.md) — record the change once implemented.

## Verification (when implemented)

`uv run poe check` green. Live ingestion of a real multi-folder tree is deferred to the
first live run (the standing no-reachable-Immich gap — see
[progress.md Notes](../progress.md#notes--blockers)); the fake-client tests are the
offline stand-in, exactly as for steps 27/28.

---
type: Playbook
title: "Folder-structure-driven ingestion (a manifest fans one tree into N albums)"
description: A root `ingest.yml` manifest declares which subfolders are album boundaries and which are segment boundaries, so `generate <root> --manifest` fans one directory tree into N independent Immich albums / album.yml files / pipeline runs / books — with folder-derived segments whose time windows come from the photos' capture timestamps.
tags: [playbook, build-order, ingest, manifest, segments, folders, fan-out, immich, cli]
timestamp: 2026-07-18
---

# Folder-structure-driven ingestion

Follow-on to the [single-command ingestion playbook](/build/single-command-ingestion.md).
That playbook made `generate <dir>` collapse "upload + create Immich album + author
`album.yml`" into one call for **one** album, with optional PDF-derived segments
(`--source-document`). This playbook generalizes the *front* of that flow: one root
directory whose **folder structure** expresses album/segment structure, driven by a
single root **manifest** (`ingest.yml`).

`generate <root> --manifest` reads the manifest, then runs the **existing** pipeline
once per declared album — one Immich album, one `album.yml`, one run, one book each.
Everything downstream of `sync` is unchanged: it already reads our DB scoped by
`album_id`, so this feature lives entirely **in front of `sync`**, exactly like the
single-command path it extends.

## What it does

```sh
photobook-generator generate <root> --manifest [ingest.yml]   # fan-out: N albums → N books
photobook-generator generate <dir>                            # flat: 1 album (today, unchanged)
photobook-generator run --album <slug>                        # album.yml-first path (unchanged)
```

`<root>/ingest.yml` declares album- and segment-boundary folders by relative path:

```yaml
albums:
  - path: lisbon-trip            # → its own Immich album + album.yml + pipeline run + book
    slug: lisbon-2026            # optional; default = slugify(folder name)
    title: Lisbon 2026           # optional; default = titleize(folder name)
    timezone: Europe/Podgorica  # optional; else the CLI --timezone default
    segments:                   # optional; each subfolder → a Segment
      - path: day-1-harbour     # label = titleize(folder name) unless overridden
      - path: day-2/old-town    # nesting is free — depth may vary between branches
        label: Old Town         # optional label override
        place: Lisbon            # optional place (no geocoding here)
  - path: wedding
    source_document: runsheet.pdf   # PDF-derived segments instead of folder segments
    lead_faces: [bride.jpg, groom.jpg]  # optional; local photos, resolved under the album path
```

A folder-derived **segment**'s time window is `[min, max]` of the capture timestamps
of the photos **in that subfolder** — never a document, never nesting depth. The
window feeds the existing `Segment`/`SegmentYAML` concept and is validated by the
**same** `AlbumYAML` loader as hand-authored segments (ordering, non-overlap,
timezone). Genericity holds trivially: the manifest and all new code speak only
"album / segment / path", never a trip, a day, or an occasion.

## Decisions (settled with the user before planning)

1. **Declaration mechanism — a root manifest (`ingest.yml`). [CONFIRMED]** One
   editable YAML file at the root maps relative paths to album/segment roles and
   carries per-album overrides. Chosen over scattered marker files (`.album`/`.segment`)
   and an interactive per-folder prompt because it is the only artifact that makes a
   re-run idempotent, it is one thing to review before a multi-GB upload, and it is the
   natural home for per-album overrides — the same "one editable YAML is the source of
   truth" posture as `album.yml`. The user's own constraint (album-vs-segment role is an
   **explicit per-folder choice**, never inferred from depth) rules out any heuristic, so
   *some* declaration is unavoidable; the manifest minimizes its surface (declare only the
   boundaries you want — undeclared subfolders just contribute their photos to the nearest
   enclosing album).
2. **Fan-out = N independent runs. [CONFIRMED]** N declared albums ⇒ N Immich albums,
   N `album.yml` files, N pipeline runs, N books. Every stage is already `album_id`-scoped
   and one Immich album == one book, so this needs **zero** pipeline/DAG/data-model change
   — only a loop in front of `sync`. It is *not* one run producing multiple outputs.
3. **Explicit opt-in, not auto-detect. [CONFIRMED]** Fan-out turns on only with
   `--manifest`. A stray `ingest.yml` in a directory never silently changes `generate <dir>`'s
   flat behavior. `--manifest` with no value defaults to `<root>/ingest.yml`.
4. **Per-album fields live in the manifest; the CLI carries only globals. [CONFIRMED]**
   In manifest mode `--slug/--title/--source-document/--lead-face` are rejected (they are
   per-album and live in the manifest — a clear error, not silent precedence). `--timezone`
   is a global default a manifest album may override; `--captioner`/`--force` apply to every
   run.
5. **Mixed content is fine, never an error. [CONFIRMED]** An album folder uploads
   *every* image under it recursively (as today). Declared segment subfolders carve out
   windows; loose images and undeclared subfolders are simply album photos in no segment —
   an already-tested downstream case (`cluster`'s time-gap fallback). Degrades cleanly to
   zero segments (a flat album folder) and to today's single-album path (no manifest).
6. **Per-album isolation. [CONFIRMED]** A malformed **manifest** fails fast before any
   upload. A bad/empty **album** (no images, undated-so-no-window, a pipeline error) is
   logged, skipped, and the run continues — mirroring the per-photo failure isolation used
   throughout the pipeline. A combined summary at the end lists the books written and the
   albums that failed and why.
7. **Build against today's upload path; stay decoupled from External Libraries.
   [CONFIRMED]** The manifest only names paths and roles; whether those files are uploaded
   (`upload_asset` + `create_album`, today) or scanned in place (a future External-Library
   path) is orthogonal, so the same manifest reuses either. No sequencing dependency.

## Folder-derived segment windows — the load-bearing detail

After upload + EXIF readiness, for each declared segment folder:

- Collect the photos under `segment.path`, map them to their Immich asset ids (kept
  from `upload_asset`), then to capture instants (from `get_album_assets`).
- Window = `[min(capture), max(capture)]`. Sort segments by `start_at` before writing so
  `Segment.position` is chronological.
- **Empty folder / all-undated photos** → no window can be formed → **skip that segment
  with a warning**; the album still generates (decision 6).
- **Single instant** (`max == min`, which the loader's `end_at > start_at` check would
  reject) → widen `end_at` to `min + 1s`.
- **Overlapping windows** (photos interleave across folders) → the shared `AlbumYAML`
  loader raises; surface it as a clear `IngestError` naming the two folders (do **not**
  silently clamp — that would misrepresent the timeline). The manifest loader also rejects
  segment paths that nest inside one another, so each photo maps to at most one segment.
- **Per album, `segments` and `source_document` are mutually exclusive** — one segment
  source, enforced in the manifest loader.

The `AlbumYAML` loader stays the single source of truth for segment correctness — the
folder path reuses it via `DirectoryIngestor._validate_materialized`, exactly as the PDF
path does today.

## Steps + parallelism

**Strictly sequential: 32 → 33 → 34 → 35.** These cannot be built concurrently. Step 33
imports step 32's models; step 34 depends on step 33's `ingest_album` seam **and** both
edit `src/album/ingest.py`; step 35 documents and verifies the finished feature. The
repo's [parallel-build precedent](/build/progress.md#notes--blockers) required a
no-shared-files check — it **fails** here (33 and 34 share `ingest.py`), so worktree
fan-out is not available for these steps. (The only work that can be drafted off the
critical path is prose — this umbrella doc and README wording — and, *within* step 33, the
two pure helpers are independent of the spine refactor.)

| # | Step | Playbook | Touches | Depends on |
|---|------|----------|---------|------------|
| 32 | Manifest schema + loader | [step-32](/build/step-32-manifest-schema.md) | new `manifest.py` | — |
| 33 | Folder-derived segments in `ingest.py` | [step-33](/build/step-33-folder-segments.md) | `ingest.py` | 32 |
| 34 | Fan-out orchestration + `generate --manifest` | [step-34](/build/step-34-fanout-cli.md) | `ingest.py`, `cli.py` | 32, 33 |
| 35 | Docs + end-to-end genericity verification | [step-35](/build/step-35-docs-verify.md) | knowledge/, README, tests | 32–34 |

Each step follows the standard workflow (branch off `main` → test-first → `poe check`
green → sub-agent review → squash → fast-forward merge → push) and updates
[progress.md](/build/progress.md) + [log.md](/log.md) on its branch before merging.

## As-built (steps 32–35, all merged)

The feature shipped as planned; the deviations worth knowing:

- **A shared `src/album/text.py`** holds `slugify`/`titleize` (step 32). Importing
  `ingest._slugify` from `manifest.py` would have become an `ingest ↔ manifest` cycle in
  step 33, so the helpers were hoisted to a leaf module both import.
- **The manifest rejects `.` (and any `..` component)** as an album/segment path — a hair
  stricter than "no escape after normalization", and it keeps an album's `effective_slug`
  well-defined (`.` would slugify an empty name). Surfaced by review.
- **The segment-source seam returns `(title_suggestion, segments)`**, not the plan's
  `list[SegmentYAML] | None` (step 33). The PDF path already produced a doc-title
  *alongside* its segments from one parse; the tuple keeps that single-parse title flowing
  through the shared `_ingest` spine without a second channel. `_validate_materialized`'s
  message was generalized to be source-agnostic (it now serves the folder path too).
- **The CLI resolves the captioner up front, inside the error guard** (step 34), so a
  misconfigured `CAPTIONER` fails fast with a clean message *before* any upload. The
  per-album pipeline catch is deliberately narrow (`FileNotFoundError, ValueError`) — an
  *unexpected* error still aborts the batch, matching `_generate_single`'s "surface real
  bugs" convention.
- **The genericity gate is a scoped token guard** (`manifest.py`/`text.py`/`ingest.py`)
  plus the fixture-tree end-to-end test (step 35), not a whole-`src/` grep — the rest of
  `src/` legitimately *discusses* occasions ("nothing here knows the occasion") and uses
  "round-trip". `ingest.py`'s module docstring dropped its one "occasion" mention so the
  three folder-ingestion modules stay token-clean.

Live multi-album fan-out against a real Immich is still folded into the standing
first-live-run gap ([progress.md Notes](/build/progress.md#notes--blockers)); the
fixture-tree end-to-end test (`test_genericity.py`) is the offline stand-in.

## What "one command" still does not remove

Same caveat as the single-command playbook: the local stack must be up (`poe up`;
`poe migrate`) and `IMMICH__API_KEY` set. This is a UX wrapper over the Immich/filesystem
boundary, not a zero-dependency runtime — stated in `--help` and the README.

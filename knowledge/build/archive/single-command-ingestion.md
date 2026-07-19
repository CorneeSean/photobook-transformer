---
type: Playbook
title: Single-command album generation (photos dir → book) + document-driven segments
description: A `photobook-generator generate <dir>` one-shot that uploads a local photo directory into Immich, materializes an album.yml, and runs the pipeline — plus an optional `--source-document=doc.pdf` that extracts segments from a schedule/itinerary via a PydanticAI agent.
tags: [playbook, build-order, cli, ingest, immich, segments, agent, pdf, ux]
timestamp: 2026-07-17
---

# Single-command album generation + document-driven segments

Follow-on to the [build order](/build/build-order.md) and
[enrichment playbook](/build/archive/enrichment.md). **Built in steps 26–28** (see
[progress](/build/progress.md) / [`log.md`](/log.md)). The shipped command is a
single `photobook-generator` with two subcommands:

```sh
photobook-generator generate <dir> [--source-document doc.pdf] [--slug] [--title] [--timezone] [--force]
photobook-generator run --album <slug>          # the album.yml-first path, unchanged
```

`generate` = "dir → book"; `run` = "existing album.yml → book".

## What it does

1. **One command, point at a folder.** `generate <dir>` collapses the manual
   [`ingest`](/stages/ingest.md) step (upload to Immich + create an album + author
   `album.yml`) into one call.
2. **Document-driven segments.** `--source-document doc.pdf` extracts `segments:`
   (time/place chapters) from an itinerary / run-sheet / lineup instead of typing them.
   Segments can *also* come from a folder's photo timestamps rather than a document —
   see the [folder-structure ingestion playbook](/build/archive/folder-structure-ingestion.md)
   (`generate <root> --manifest`), which reuses this same `DirectoryIngestor` spine.

Both are **additive** — no stage logic, data-model, or DAG change. Everything
downstream of `sync` is already directory-agnostic (reads our DB, scoped by
`album_id`), so the feature lives entirely **in front of `sync`**.

**What "one command" does *not* remove:** the local stack still has to be up
(`poe up`; `poe migrate`) and `IMMICH__API_KEY` set (`ANTHROPIC_API_KEY` only for
`--captioner claude`). It's a UX wrapper over the boundary, not a zero-dependency
runtime — stated plainly in `--help` and the README.

## Feature A — single-command ingestion

**Flow** (`generate <dir>`): resolve slug/title → walk `<dir>` for images, upload
each via `upload_asset` (content-deduped, idempotent) → `create_album` → poll Immich
job readiness (EXIF at minimum; reuses `LeadFaceResolver`'s async-poll shape, warns
on timeout) → write `data/albums/<slug>/album.yml` → call the **existing**
`pipeline(album_slug=slug)` → print the `book/index.html` path.

**Code:** `src/album/immich/client.py` grew `create_album` (`POST /albums`) +
`get_jobs` (`GET /jobs`, the readiness signal); `src/album/ingest.py` (new
top-level module — *not* a stage; walk → upload → create → poll → materialize) owns
the boundary and returns the slug; `cli.py` stays thin (arg-parse → `ingest` →
`pipeline`).

**`album.yml` stays the single source of truth** — `generate` *materializes* it and
uses the normal load path, so it's inspectable/editable. `get_or_create_album` is
load-once-per-slug: re-running on an existing slug reuses the `Album` row and does
**not** re-read a regenerated `album.yml` — hence `--force` re-runs an existing slug
as-is (no re-upload/orphan/clobber). Genericity holds trivially: `ingest.py` deals in
"a directory of images" + "an Immich album", no occasion concept.

### Title resolution

No title is required; resolved by precedence (explicit wins, then cheaper inference):
1. **`--title`** (and `--slug`; default slug = directory name slugified).
2. **From `--source-document`** — the extraction agent returns a suggested `title`
   alongside the segments, same call, no extra round-trip.
3. **From photo metadata** — deterministic: EXIF capture date range (+ geocoded place
   once consumed) → "Lisbon · May 2026". Date-only where place isn't wired.
4. **Directory name** → "Trip 2026".
5. **Slug** — last resort.

A place+date title names no occasion concept, so genericity holds.

## Feature B — source document → segments

**App feature, not a Claude skill.** A `.claude/skills` bundle would be dev-time
only, unavailable to end users, and untestable. The durable path is an in-app
PydanticAI agent — reproducible, `TestModel`-testable, available to every user. A
thin `.claude/skills` wrapper, if wanted, should just invoke the same command, not
re-implement extraction.

**`src/album/agents/segments.py`** — `extract_segments` / `extract_segments_from_pdf`
(pypdf text; scanned-PDF fallback sends page images as `BinaryContent`). Output is a
wrapper over `SegmentYAML` + an optional `title`. Pure and DB-free; `ingest.py` writes
the result into the `album.yml`'s `segments:` block (omitted without the flag).

- **Grounding.** Runs **after** upload + EXIF readiness so the agent gets the photos'
  capture date range + album `timezone` as anchors — turning "Day 1" into a real
  UTC-normalizable window. It emits ISO timestamps; `AlbumYAML`'s validator normalizes
  to UTC and enforces non-overlap/ordering (loader stays the correctness authority).
- **Genericity constraint (load-bearing).** The document may *be* an itinerary, but the
  module/model/fields/prompt talk about "a document describing time/place windows" —
  never "itinerary", "day", "trip". Mixed examples (trip/wedding/festival) in the
  prompt keep extraction generic. Enforced in review + tests.

## Deferred / still open

- **Live-Immich readiness timing** and **live-Claude extraction accuracy** — the same
  no-reachable-Immich / no-`ANTHROPIC_API_KEY` sandbox gaps as steps 4–5/9; re-verify
  on first live run.
- Out of scope: physical printing (`LuluPrinter` + PDF-hosting gap), the deferred
  LAION/open_clip scorer, any stage/data-model change.

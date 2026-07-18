---
type: Build Playbook
title: "Step 35: Docs + end-to-end genericity verification for folder-driven ingestion"
description: Plan for the cross-cutting docs (README, folder-structure.md, album-definition.md, the umbrella playbook's as-built notes) plus an end-to-end test that fans a fixture multi-album tree into books and guards that no occasion concept leaked into src/.
tags: [build, docs, genericity, verification, manifest, plan]
timestamp: 2026-07-18
---

# Step 35: Docs + end-to-end genericity verification

**Status: todo.** Depends on steps 32–34 (documents and verifies the finished
feature). Final step of the
[folder-structure-driven ingestion playbook](/build/folder-structure-ingestion.md).
Steps 32–34 each update their own local docs at merge (per convention); this step is
the **cross-cutting** docs + the end-to-end guard that belongs to no single code step.

## Why a separate step

Two things don't fit inside a code step: the **user-facing** surface (README + the
reference docs a user reads to author `ingest.yml`) and an **end-to-end** test that
exercises the whole fan-out on a fixture tree and asserts the genericity rule held
across all three steps. Bundling them here keeps steps 32–34 tight and gives the
feature one explicit "prove it" gate — the same role step 16 played for the base
abstraction.

## Docs

- **README** — a "Multiple albums from one folder tree" subsection under the
  `generate` docs: the `generate <root> --manifest` command, an annotated `ingest.yml`
  example, the per-album-flags-live-in-the-manifest rule, and the same "stack must be
  up / `IMMICH__API_KEY` set" caveat the single-command section already carries.
- [folder-structure.md](/reference/folder-structure.md) — confirm the `manifest.py`
  row (added in step 32) and note `ingest.py` now also fans out (`ingest_manifest`);
  the `data/albums/<slug>/` note already covers the N-album output (one dir per slug).
- [album-definition.md](/reference/album-definition.md) — cross-reference: segments may
  be authored by hand, extracted from a `--source-document`, **or** derived from
  subfolders via a manifest; the on-disk `album.yml` shape is identical either way.
- [folder-structure-ingestion.md](/build/folder-structure-ingestion.md) (umbrella) —
  add an "As-built" preamble reconciling any implementation-time deviations across
  steps 32–34 (the step-30/31 playbooks set the precedent for this).
- [progress.md](/build/progress.md) / [log.md](/log.md) — final playbook-complete
  note; move the step-35 row Outstanding → Completed on merge.

## End-to-end genericity test

New fixture tree under `tests/` (a couple of tiny images in nested subfolders) + an
`ingest.yml`, plus a test that drives `ingest_manifest` with a fake `IngestClient` and
patched `pipeline` (no live Immich), asserting:

- N declared albums → N `AlbumOutcome`s → N `pipeline(album_slug=...)` calls (one per
  slug) and N `album.yml` files materialized under `data/albums/<slug>/`.
- A folder-derived album's `album.yml` carries the expected segment windows; a flat
  album folder (no segment subfolders) materializes **zero** segments (segments-optional
  guard end-to-end).
- **Genericity guard** — extend the existing genericity check (the step-16
  `src/`-scanning test / grep guard, e.g. `test_genericity`) so the manifest path is
  covered: no trip/day/itinerary/occasion token in `src/album/manifest.py` or the new
  `ingest.py` code. This is the "enforced in tests, not prose" line from
  [folder-structure.md](/reference/folder-structure.md).

Follows the `smith-wedding` genericity precedent (step 16): a second album *shape*
proves the abstraction without any `src/` special-casing.

## Verification (when implemented)

`uv run poe check` green, including the new end-to-end test. Live verification of a real
multi-folder tree → multiple books remains folded into the standing first-live-run gap
([progress.md Notes](/build/progress.md#notes--blockers)); the fixture-tree end-to-end
test is the offline stand-in and the genericity gate.

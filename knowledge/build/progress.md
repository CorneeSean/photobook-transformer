---
type: Progress Tracker
title: Build progress
description: Living per-step status for the build order — updated by the implementing agent as each step moves through the workflow.
tags: [progress, status, tracking, build-order]
timestamp: 2026-07-12
---

# Build progress

Living status of the [build order](/build/build-order.md), maintained by the
implementing agent as part of the [implementation workflow](/process/implementation-workflow.md).
Update it whenever a step changes state. Steps are delivered strictly in order, one
at a time. Per-step narrative and rationale live in [log.md](/log.md); this tracker
is status only.

**Status values:** `todo` → `in-progress` → `agent-review` → `awaiting-user` →
`done` (`blocked` with a note if a step can't proceed).

**Bookkeeping rule:** a step stays in **[Outstanding](#outstanding-todo)** while
`todo`/`in-progress`/`blocked` (set `in-progress` + record its branch when work
begins); the moment it merges to `main` it **moves into the matching
[Completed](#completed-done)** sub-table with its commit hash. Keep the split
accurate — it is part of the workflow, not optional tidiness.

## Outstanding (TODO)

Take the **lowest-numbered** step. The rest is optional/deferred — nothing is blocked
on missing code (see [Notes / blockers](#notes--blockers)).

| # | Step | Playbook | Status | Branch | Notes |
|---|------|----------|--------|--------|-------|
| 23 | DB-backed flow smoke test (optional) | testcontainers | todo | — | largely subsumed by step 25's `test_flow_integration.py`; revisit only if a gap remains |

The folder-structure-driven ingestion playbook (steps 32–35) is **complete** — see
[Completed](#folder-structure-driven-ingestion-playbook-see-folder-structure-ingestionmd).
The **external-library / scan-in-place ingestion** playbook (steps 36–38, an opt-in
mode; upload stays the default) is **complete** — all three steps (plumbing, the
`AssetRegistrar` seam, the `--ingest-mode` flag + docs) are done and merged (see
[Completed](#external-library-ingestion-playbook-complete-see-external-library-ingestionmd)).
The
[decisions](/build/external-library-ingestion.md#decisions-confirmed-with-the-user-2026-07-19-before-step-36)
were confirmed with the user 2026-07-19. **Step 23 is the only outstanding step,
and it is optional** — nothing else remains on the build order.

## Completed (DONE)

Audit trail — one line per step; see the commit and [log.md](/log.md) for detail.

### Build order

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 0 | Scaffold | step-00-scaffold | 11d290f | `config.py`/`definition.py`/`flow.py` deferred to steps 3/14 |
| 1 | Containers | step-01-containers | 240fbe0 | Immich stack vendored as its own file via Compose `include:` |
| 2 | Data model + migrations | step-02-data-model | 68ed96f | |
| 3 | Album definition loading | step-03-album-definition | 84260ef | |
| 4 | Immich client | step-04-05-immich-client-sync | 8172dfa | with step 5; resolves Open Q1 (no CLIP embeddings); fixtures from OpenAPI, not live |
| 5 | `sync` stage | step-04-05-immich-client-sync | 8172dfa | with step 4; live run deferred (no reachable Immich) |
| 6 | `quality` stage | step-06-quality | a6c4b9e | real OpenCV blur; aesthetic/CLIP injected — real LAION/open_clip deferred (heavy) |
| 7 | `cluster` stage | step-07-cluster | 2a39df5 | temporal + spatial(DBSCAN) scenes; no-segments/no-GPS fallbacks tested |
| 8 | `select` stage (MMR) + `db/similarity.py` | step-08-select | 4fec4a1 | real pgvector query verified on a Postgres testcontainer |
| 9 | Agent (`analysis.py`) | step-09-agent | 38a4057 | tested via `TestModel`; real-Claude check deferred (no key). Retired in step 29 |
| 10 | `enhance` stage | step-10-enhance | e7fccfe | shared idempotent `ensure_analysis()` (reused by step 12); per-scene exposure baseline; XMP sidecars |
| 11 | `render` stage | step-11-render | 168940f | adds `ImmichClient.get_original()`; darktable manual check deferred (closed by step 22) |
| 12 | `caption` stage | step-12-caption | 69f82b3 | thin wrapper over `ensure_analysis()`, order-independent with `enhance`; spot-check deferred |
| 13 | `assemble` stage | step-13-assemble | 9ec5ca7 | resolves Open Q4 (Lulu 8.5×8.5in, 0.125in bleed, spine = pages/444+0.06in); adds `Album.assembled_pdf_path` |
| 14 | Wire the flow | step-14-wire-flow | ada27d6 | real Postgres+Prefect verified; fails cleanly at `sync` (no Immich); LAION scorer still deferred |
| 15 | `fulfill` stage | step-15-fulfill | 92a7d4c | Lulu client (OAuth2); `lulu_order_id` → `output_ref` post-step-16; no PDF hosting yet; sandbox order deferred |
| 16 | Prove genericity | step-16-prove-genericity | d7e903d | adds `smith-wedding/album.yml` + genericity test; zero `src/` touched |

### Enrichment playbook (see [enrichment.md](/build/archive/enrichment.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 17 | Faces in client + `sync` (+schema) | step-17-faces-sync | c99d319 | `get_faces`; `Photo.face_count`/`person_ids` + album-scoped `Person` table; failure-isolated N+1; genericity guard holds |
| 17b | Lead-face resolution | step-17b-lead-faces | 06810d7 | `LeadFacesYAML` + `LeadFaceResolver` (upload + async-detection poll); marks `Person.is_lead` (exact set) |
| 18 | People/lead-aware `select` | step-18-people-select | f3afc54 | effective-score boosts + lead guaranteed coverage; strict on non-leads; byte-for-byte for face-free albums |
| 19 | Name-grounded `caption` (optional) | step-19-name-caption | cd77da6 | `_resolve_person_names` (one query/album); rides the shared analysis call; byte-for-byte face-free; spot-check deferred |
| 20 | Rating + geocoded place (OCR deferred) | worktree-step-20-rating-place | bb430d2 | `rating` → soft `select` boost; place → `caption` grounding; `cluster` untouched; free off `sync`; OCR + live re-verify deferred |

*(Enrichment playbook complete.)*

### TestContainers playbook (see [testcontainers.md](/build/testcontainers.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 21 | pgvector schema + integration harness | step-21-pgvector-integration | ac2cd14 | alembic-migrated pgvector container; `select` over the real query; no drift; +`poe test-fast` |
| 22 | darktable-cli render integration | step-22-darktable-render | 8aac81b | real render in the app image, visibly brighter; validated the darktable XMP history-stack format; closes step 11's manual check |
| 24 | Immich ML integration (offline baked image) | step-24-immich-ml-integration | ad86550 | out of order (23 still todo); pre-baked CLIP+`buffalo_l` ONNX image; real 4-service stack; closes the faces+CLIP live half; opt-in, skipped in `poe check` |
| 25 | Parallelize the flow (fan-out groups) | step-25-parallelize-flow | 036fcc2 | fan-out DAG per `pipeline-execution.md`; fixed the `enhance`‖`caption` idempotency race (join `enhance` first); real-concurrency integration test |

*(Step 23 is outstanding — see [Outstanding (TODO)](#outstanding-todo).)*

### Single-command ingestion playbook (see [single-command-ingestion.md](/build/archive/single-command-ingestion.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 26 | Immich album creation in the client | step-26-immich-album-create | 94fba06 | `create_album` + `get_jobs` (step 27's readiness signal); OpenAPI-modelled — re-verify live |
| 27 | `ingest.py` + `generate` subcommand (Feature A) | step-27-ingest-cli | c1c5146 | `DirectoryIngestor` → the unchanged `pipeline`; single `photobook-generator` CLI; `--force` re-runs a slug safely; live ingestion deferred |
| 28 | `agents/segments.py` + `--source-document` (Feature B) | step-28-segments-agent | 1e2b0af | PydanticAI segment extraction (pypdf + scanned fallback); occasion-agnostic; validated fail-fast; extraction spot-check deferred |

### Offline analysis playbook (see [local-analysis.md](/build/archive/local-analysis.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 29 | Classical replacement for the Claude vision call | step-29-offline-analysis | ba6c1bf | dropped the **last external-API dependency** — `ClassicalToneEstimator` + `TemplateCaptioner` default, `ClaudeCaptioner` opt-in (`--captioner`); retired `agents/analysis.py`; 4-agent review; caption spot-check deferred |

### Lead-face CLI playbook (see [step-30-cli-lead-faces.md](/build/archive/step-30-cli-lead-faces.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 30 | CLI flag for multiple lead-face reference photos | step-30-cli-lead-faces | e50a54a | repeatable `--lead-face IMAGE` on `generate`/`run` writes/patches `lead_faces.reference_images`; `LeadFacesYAML` → three symmetric list inputs (single = one-element, no singular forms). Verify-plan-first caught it orphaning the singular path; adversarial review caught the plan's count-keyed cache silently keeping the old lead on a same-count swap → **dropped the cache**, rely on Immich content-dedup (migration `a7e3f9c21b84` drops `Album.lead_reference_asset_id`). 3-agent review, all findings applied; `poe check` green (341/3); live multi-photo verify deferred |

### Aesthetic scorer playbook (see [step-31-laion-aesthetic-scorer.md](/build/archive/step-31-laion-aesthetic-scorer.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 31 | Wire the real LAION/open_clip aesthetic scorer | step-31-laion-aesthetic-scorer | 2446901 | closes the step-6/14 deferral: `ClipAestheticModel` (ViT-L/14 CLIP + LAION head) behind the existing `AestheticScorer`/`Embedder` seam; `scoring` extra (torch out of base deps); capability-gated with graceful fallback to the NULL-scoring placeholder; one shared model backs both protocols with a one-entry feature memo; no migration (`clip_embedding` already `Vector(768)`). Offline monotonicity verified via `poe test-scoring`; live within-scene curation spot-check deferred (no reachable Immich) |

### Folder-structure-driven ingestion playbook (see [folder-structure-ingestion.md](/build/archive/folder-structure-ingestion.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 32 | Ingest manifest schema + loader | step-32-manifest-schema | 445357f | pure `src/album/manifest.py` — `Manifest`/`AlbumSpec`/`SegmentSpec` (`extra="forbid"`) + `load_manifest()`; relationship validation (path escape, dup paths, resolved-slug collision, component-prefix segment non-nesting, `segments` XOR `source_document`), path existence deferred to ingest. `slugify`/`titleize` hoisted to shared `src/album/text.py` to break the step-33 `ingest↔manifest` cycle. 3-agent review (2× correctness, style): no defects; applied token-cleanup (no trip/day/itinerary in src) + `a/../b` as-built tightening. `poe check` green (370/6) |
| 33 | Folder-derived segments in `ingest.py` | step-33-folder-segments | 28c3ba0 | `DirectoryIngestor.ingest_album(spec, root)` derives each subfolder's `[start_at, end_at]` from its photos' capture timestamps, behind a `SegmentSource` seam shared with the PDF path (spine refactored into `_ingest`; flat/PDF path behavior-preserving). Undated/empty folders skip with a warning (→ zero-segment album, byte-identical to flat), single-instant widens +1s, overlap caught by the shared `AlbumYAML` loader. Manifest hardened to reject `path: "."`. 4-agent review (2× correctness, style/reuse, architecture): no defects; keep the title-suggestion tuple seam (doc-title co-product predates step 33); applied DRY `_document_source` + cleanups. `poe check` green (389/6); live multi-folder ingest deferred |
| 34 | Fan-out orchestration + `generate --manifest` | step-34-fanout-cli | 0e67990 | `ingest_manifest`/`_ingest_all` + `AlbumOutcome`: ingest N albums under ONE shared `ImmichClient` with per-album isolation (ingest.py stays pipeline-free). CLI `generate --manifest [FILE]` (opt-in) fans the tree into N books, rejecting per-album flags, threading globals, running the pipeline per album with isolation, and printing a combined summary (exit non-zero only if every album failed). 4-agent review (2× correctness, style/reuse, architecture): no correctness defects; applied captioner fail-fast (inside the guard, before uploads) + `_book_path` DRY helper. As-built: narrow pipeline catch surfaces unexpected errors (deliberate). `poe check` green (404/6); live fan-out deferred |
| 35 | Docs + end-to-end genericity verification | step-35-docs-verify | 38a3aee | Closes the playbook. User docs (README multi-album section, `album-definition.md`/`folder-structure.md` cross-refs, umbrella "As-built" preamble). Verification gate: a scoped token guard (`manifest.py`/`text.py`/`ingest.py` carry no trip/day/itinerary/occasion vocabulary — whole-`src/` grep infeasible) + an end-to-end test over a committed two-album fixture tree (`tests/fixtures/manifest_tree`) driving real `load_manifest`+`_ingest_all`+materialization+patched pipeline (N albums → N `album.yml`, one folder-windowed + one zero-segment → N pipeline runs). `DatingIngestClient` hoisted to `tests/doubles.py`. 2-agent review (adversarial + style/docs): caught a stray `open-questions.md` frontmatter corruption (reverted); gate sound. `poe check` green (408/6); live fan-out deferred |

### External-library ingestion playbook (complete) (see [external-library-ingestion.md](/build/external-library-ingestion.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 36 | Plumbing (config + path translation + client `/libraries` methods + Docker mount) | step-36-external-library-plumbing | 2279207 | Foundation for opt-in scan-in-place ingest, no behavior wired yet: `ImmichSettings.ingest_mode`/`external_host_root`/`external_container_root`; pure `PathMapping` (`immich/paths.py`) host↔container translation; `ImmichClient` External-Library surface (`get_current_user_id`/`get_libraries`/`create_library`/`scan_library`/`get_library_assets`) + `originalPath` on `ImmichAsset`, sharing a new `_search_metadata` helper with `get_album_assets`; opt-in commented `${PHOTOS_LOCATION}:/external:ro` mount + `.env.example`. 2-agent review (adversarial correctness + style/reuse): no blockers/majors; nits applied. `/libraries` methods modelled on documented API, respx-tested only — **live-verify shape on first reachable Immich** (live-integration gap). Static gate + all non-container tests green; container tests were blocked at merge by an unresponsive Docker daemon (env, not code), accepted by the user. **Re-verified 2026-07-19 with Docker running: the container-backed suite passes (44 passed, incl. the pgvector testcontainer).** `_wait_for_readiness` scan-queue extension deferred to step 37 |
| 37 | The `AssetRegistrar` seam (extract `UploadRegistrar`; add `ExternalLibraryRegistrar`; inject into `DirectoryIngestor`) | step-37-asset-registrar | 749a57e | Wires the step-36 plumbing behind an opt-in seam (default upload unchanged). `_ingest`'s inline `{path: upload_asset(path)}` → an injected `AssetRegistrar.register(directory, image_paths) → {host path → asset id}`; everything downstream reads only that map. `UploadRegistrar` (today's upload, extracted verbatim, default) + `ExternalLibraryRegistrar` (ensure one shared library reused by import path → scan → wait the `library` queue → map by `originalPath` through `PathMapping`, filter tree-wide library to this album, fail-fast naming unscanned photos). Readiness poll generalized to a shared `_wait_for_queues(get_jobs, queues, …)` so each strategy declares its queues (EXIF for the ingestor, scan queue for the registrar). `build_registrar` maps `ingest_mode` → registrar (validation out of `config.py`, mirrors `build_captioner`); `ingest_directory`/`ingest_manifest` build it from settings. Complexity gate split `_map_assets` (rank B → helpers). Inline review (correctness/reuse/simplification/altitude): no defects; stale "upload" doc comments refreshed. **Live-verify caveat:** async scan means the `library`-queue poll's initial idle check could race a not-yet-enqueued job on a real Immich — folded into the live-integration gap. `poe check` green (434/6, incl. pgvector testcontainer). `--ingest-mode` flag + user docs are step 38 |
| 38 | The `--ingest-mode` flag + docs (closes the playbook) | step-38-ingest-mode-flag | f356c2b | Exposes the step-37 seam on the CLI: `generate --ingest-mode {upload,external}`, flag > `IMMICH__INGEST_MODE` env > `upload` default (mirrors `--captioner`). Run-level (applies to every album incl. `--manifest` fan-out), so it is **not** a per-album flag and is never rejected. `INGEST_MODES` centralizes the valid set the CLI `choices` and `build_registrar` dispatch both read; `build_registrar` takes an `ingest_mode` override threaded from `ingest_directory`/`ingest_manifest`. Docs: README "Scan photos in place (no second copy)" section + ingest stage-doc note (mount, `EXTERNAL_HOST_ROOT`, admin-key, originals-must-stay-mounted trade-off). Default upload path unchanged; new CLI/precedence tests + a `--help` smoke. Inline review: no defects. `poe check` green (438/6). Live external run against a real Immich + bind mount stays in the live-integration gap (no reachable Immich here) |

### CLI unification (user-requested, 2026-07-19)

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 39 | Fold `generate`+`run` into one strategy-based `build` command | step-39-unify-cli-build | 22acb9c | User-requested cleanup: one `photobook-generator build` with two switchable phases decided once args are parsed into an `AlbumSource` strategy (ABC). `build <dir>` = `DirectoryIngestSource` (ingest one + run); `--manifest` = `ManifestIngestSource` (fan-out); `--no-ingest --album <slug>` = `NoIngestSource` (skip ingest, run existing `album.yml` — the old `run`). `_select_source` picks the strategy; a single `_run_pipelines`+`_report` driver runs the pipeline over each `PreparedAlbum` and prints one combined summary — replacing the scattered `_generate*`/`_run` handlers. **Surface decisions (confirmed with user):** new verb `build`; `--no-ingest` (explicit) to skip; **clean break, no `generate`/`run` aliases**. Argument misuse (missing `DIR`, `--album` sans `--no-ingest`, per-album flags in fan-out, ingest options with `--no-ingest`) is now a uniform argparse usage error (exit 2, was 1 for per-album). `test_cli.py` rewritten; `test_genericity.py` retargeted to `_run_pipelines`/`_prepared_from_outcome`; poe `run`/`generate` tasks → `build`; docs (README.md/.html, AGENTS, working-agreement, folder-structure, ingest stage, external-library playbook) updated. Inline review (correctness/reuse/simplification/altitude): no defects; `_select_source` split (rank B → A). `poe check` green (440/6) |

## Notes / blockers

**Current status.** Every build-order + enrichment + testcontainers + single-command-ingestion
+ offline-analysis + folder-structure-ingestion step is done and merged, plus the lead-face CLI
follow-on (**step 30**), the real LAION aesthetic scorer (**step 31**), the **complete
external-library ingestion playbook** (**steps 36–38**: plumbing + the `AssetRegistrar` seam +
the `--ingest-mode` flag), and the **CLI unification** (**step 39**: one `build` command). The
only outstanding step is the optional **step 23** (largely subsumed by step 25's
`test_flow_integration.py`). The pipeline is **fully offline by default**
(classical tone + template caption, no API/key); Claude is opt-in per run (`--captioner claude`,
`--force` for A/B). Under `--force`, `enhance` and `caption` each re-run the shared analysis for a
clustered photo — an accepted ~2× cost on paid Claude A/B; prefer a fresh slug for a large online run.

**Standing process notes.**
- **Human-review gate waived (step 4 onward, per user):** after `poe check` green + sub-agent
  review clean, squash and fast-forward-merge autonomously.
- **Sub-agent review panel is up to 4 (2026-07-17):** 2 adversarial correctness + style/reuse +
  architecture ([implementation-workflow.md](/process/implementation-workflow.md) §6); scale down for small diffs.
- **`main` is commit-protected:** update the tracker + `log.md` on the step branch *before* the
  fast-forward merge.
- **Parallel-build precedent (2026-07-14, user-approved exception):** steps 10/11/12/13/15 were built
  concurrently on separate worktrees after a no-shared-files check. Not a new default.

**Open blockers — all "no reachable live service in this sandbox"; nothing blocked on code:**
- **Live-integration gap.** No reachable Immich, `ANTHROPIC_API_KEY`, host `darktable-cli` (only in
  the app image), or Lulu sandbox creds. A full run verified against real Postgres+Prefect but fails
  cleanly at `sync` (`ConnectionRefused`). Deferred to first live run: honeymoon + wedding re-verify,
  faces/rating/geocode on real assets, live-Claude caption spot-check (9/10/12/19/28/29), a real
  darktable render, and a single-copy Lulu test order (+ confirm `GET /print-jobs/{id}/` schema).
  Step 24's offline ML stack already closed the faces + CLIP half.
- **No PDF-hosting for `LuluPrinter` (step 15).** `Album.assembled_pdf_path` is a local path; Lulu's
  `source_url` needs a fetchable `http(s)://` URL, so `LuluPrinter.print` raises `UnreachablePdfError`.
  Only affects the opt-in `LuluPrinter` (default `HtmlPrinter` needs none).
- **Real LAION/open_clip scorer — wired (step 31, 2026-07-18).** `ClipAestheticModel` (ViT-L/14 CLIP
  + LAION head) now backs the `AestheticScorer`/`Embedder` seam, behind the opt-in `scoring` extra and
  capability-gated with a graceful fallback to the `_UnavailableQualityModel` placeholder (unchanged
  NULL-score behavior when the extra/weights are absent). Offline monotonicity verified via
  `poe test-scoring`. *Remaining, live-only:* confirm `select` picks the visibly better frame within a
  scene on a real album (needs reachable Immich) — folded into the live-integration gap above.

**Toolchain landmine.** `ruff` 0.15.21's formatter under `target-version = "py314"` rewrites
`except (A, B):` into invalid paren-less syntax — use separate `except` clauses or `# fmt: skip`.
See [log.md](/log.md) 2026-07-13.

**Resolved landmines (kept as record).** Ryuk socket-mount failure went away with a Docker reinstall
(2026-07-15) — plain `poe check` is green; `PostgresContainer` still needs `driver="psycopg"` (psycopg3).
The `output_ref` rename (2026-07-14) and the `db/queries.py` hoist (Open Q7) are done — see the DONE
tables and [open-questions.md](/build/open-questions.md).

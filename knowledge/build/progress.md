---
type: Progress Tracker
title: Build progress
description: Living per-step status for the build order — updated by the implementing agent as each step moves through the workflow.
tags: [progress, status, tracking, build-order]
timestamp: 2026-07-12
---

# Build progress

Living status of the [build order](build-order.md), maintained by the
implementing agent as part of the [implementation workflow](../process/implementation-workflow.md).
Update it whenever a step changes state. Steps are delivered strictly in order, one
at a time. Per-step narrative and rationale live in [log.md](../log.md); this tracker
is status only.

**Status values:** `todo` → `in-progress` → `agent-review` → `awaiting-user` →
`done` (`blocked` with a note if a step can't proceed).

**Bookkeeping rule:** a step stays in **[Outstanding](#outstanding-todo)** while
`todo`/`in-progress`/`blocked` (set `in-progress` + record its branch when work
begins); the moment it merges to `main` it **moves into the matching
[Completed](#completed-done)** sub-table with its commit hash. Keep the split
accurate — it is part of the workflow, not optional tidiness.

## Outstanding (TODO)

_None — all queued work is merged to `main`._ New or deferred-item work follows the
[implementation workflow](../process/implementation-workflow.md); add its row here when
it starts.

Every build-order step (0–16) and every follow-on playbook — enrichment (17–20),
TestContainers (21–25), single-command ingestion (26–28), offline analysis (29),
folder-structure ingestion (32–35), external-library ingestion (36–38) — plus the
lead-face CLI (30), the LAION scorer (31), the CLI unification (39, incl. the
`album/cli/` package split), per-album curation tuning (40), total-failure
escalation + logging (41), the caption one-sentence summary + configurable
output language (42), and the master PDF rendered on demand in `LuluPrinter`
(43) is done and merged; see [Completed](#completed-done). The
optional **step 23** was closed as subsumed (2026-07-19). Live-integration
verification (no reachable Immich / `ANTHROPIC_API_KEY` / `darktable-cli` / Lulu creds
in this sandbox) is tracked in [Notes / blockers](#notes--blockers); nothing else is
blocked on code.

## Completed (DONE)

Audit trail — one line per step; see the commit and [log.md](../log.md) for detail.

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

### Enrichment playbook (see [enrichment.md](archive/enrichment.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 17 | Faces in client + `sync` (+schema) | step-17-faces-sync | c99d319 | `get_faces`; `Photo.face_count`/`person_ids` + album-scoped `Person` table; failure-isolated N+1; genericity guard holds |
| 17b | Lead-face resolution | step-17b-lead-faces | 06810d7 | `LeadFacesYAML` + `LeadFaceResolver` (upload + async-detection poll); marks `Person.is_lead` (exact set) |
| 18 | People/lead-aware `select` | step-18-people-select | f3afc54 | effective-score boosts + lead guaranteed coverage; strict on non-leads; byte-for-byte for face-free albums |
| 19 | Name-grounded `caption` (optional) | step-19-name-caption | cd77da6 | `_resolve_person_names` (one query/album); rides the shared analysis call; byte-for-byte face-free; spot-check deferred |
| 20 | Rating + geocoded place (OCR deferred) | worktree-step-20-rating-place | 698757f | `rating` → soft `select` boost; place → `caption` grounding; `cluster` untouched; free off `sync`; OCR + live re-verify deferred |

*(Enrichment playbook complete.)*

### TestContainers playbook (see [testcontainers.md](archive/testcontainers.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 21 | pgvector schema + integration harness | step-21-pgvector-integration | ac2cd14 | alembic-migrated pgvector container; `select` over the real query; no drift; +`poe test-fast` |
| 22 | darktable-cli render integration | step-22-darktable-render | 8aac81b | real render in the app image, visibly brighter; validated the darktable XMP history-stack format; closes step 11's manual check |
| 23 | DB-backed flow smoke test (optional) | — | — (subsumed) | **Closed as subsumed, 2026-07-19** — not implemented separately. Its stated goal (real Postgres container, whole Prefect flow, Immich/Claude/Lulu doubled, cross-stage data flow + real per-stage commits) is already met by step 25's `tests/album/flow/test_flow_integration.py` (real `pipeline()` end-to-end on the pgvector container, all external boundaries doubled, asserts every stage's columns via `expire_all()` read-after-write) plus step 21's `test_pgvector_integration.py` (real `select`/`cluster` query + near-dup guard). Tracker's "revisit only if a gap remains" condition not met; a standalone test would be a near-duplicate |
| 24 | Immich ML integration (offline baked image) | step-24-immich-ml-integration | f990248 | built out of order (ahead of 23, which was later closed as subsumed); pre-baked CLIP+`buffalo_l` ONNX image; real 4-service stack; closes the faces+CLIP live half; opt-in, skipped in `poe check` |
| 25 | Parallelize the flow (fan-out groups) | step-25-parallelize-flow | 036fcc2 | fan-out DAG per `pipeline-execution.md`; fixed the `enhance`‖`caption` idempotency race (join `enhance` first); real-concurrency integration test |

*(Playbook complete — step 23 closed as subsumed, 2026-07-19.)*

### Single-command ingestion playbook (see [single-command-ingestion.md](archive/single-command-ingestion.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 26 | Immich album creation in the client | step-26-immich-album-create | 1e69505 | `create_album` + `get_jobs` (step 27's readiness signal); OpenAPI-modelled — re-verify live |
| 27 | `ingest.py` + `generate` subcommand (Feature A) | step-27-ingest-cli | 2b0bd56 | `DirectoryIngestor` → the unchanged `pipeline`; single `photobook-generator` CLI; `--force` re-runs a slug safely; live ingestion deferred |
| 28 | `agents/segments.py` + `--source-document` (Feature B) | step-28-segments-agent | e398662 | PydanticAI segment extraction (pypdf + scanned fallback); occasion-agnostic; validated fail-fast; extraction spot-check deferred |

### Offline analysis playbook (see [local-analysis.md](archive/local-analysis.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 29 | Classical replacement for the Claude vision call | step-29-offline-analysis | 92b020e | dropped the **last external-API dependency** — `ClassicalToneEstimator` + `TemplateCaptioner` default, `ClaudeCaptioner` opt-in (`--captioner`); retired `agents/analysis.py`; 4-agent review; caption spot-check deferred |

### Lead-face CLI playbook (see [step-30-cli-lead-faces.md](archive/step-30-cli-lead-faces.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 30 | CLI flag for multiple lead-face reference photos | step-30-cli-lead-faces | c695e09 | repeatable `--lead-face IMAGE` on `generate`/`run` writes/patches `lead_faces.reference_images`; `LeadFacesYAML` → three symmetric list inputs (single = one-element, no singular forms). Verify-plan-first caught it orphaning the singular path; adversarial review caught the plan's count-keyed cache silently keeping the old lead on a same-count swap → **dropped the cache**, rely on Immich content-dedup (migration `a7e3f9c21b84` drops `Album.lead_reference_asset_id`). 3-agent review, all findings applied; `poe check` green (341/3); live multi-photo verify deferred |

### Aesthetic scorer playbook (see [step-31-laion-aesthetic-scorer.md](archive/step-31-laion-aesthetic-scorer.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 31 | Wire the real LAION/open_clip aesthetic scorer | step-31-laion-aesthetic-scorer | 0cafbdb | closes the step-6/14 deferral: `ClipAestheticModel` (ViT-L/14 CLIP + LAION head) behind the existing `AestheticScorer`/`Embedder` seam; `scoring` extra (torch out of base deps); capability-gated with graceful fallback to the NULL-scoring placeholder; one shared model backs both protocols with a one-entry feature memo; no migration (`clip_embedding` already `Vector(768)`). Offline monotonicity verified via `poe test-scoring`; live within-scene curation spot-check deferred (no reachable Immich) |

### Folder-structure-driven ingestion playbook (see [folder-structure-ingestion.md](archive/folder-structure-ingestion.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 32 | Ingest manifest schema + loader | step-32-manifest-schema | 50c83ab | pure `src/album/manifest.py` — `Manifest`/`AlbumSpec`/`SegmentSpec` (`extra="forbid"`) + `load_manifest()`; relationship validation (path escape, dup paths, resolved-slug collision, component-prefix segment non-nesting, `segments` XOR `source_document`), path existence deferred to ingest. `slugify`/`titleize` hoisted to shared `src/album/text.py` to break the step-33 `ingest↔manifest` cycle. 3-agent review (2× correctness, style): no defects; applied token-cleanup (no trip/day/itinerary in src) + `a/../b` as-built tightening. `poe check` green (370/6) |
| 33 | Folder-derived segments in `ingest.py` | step-33-folder-segments | 28aa809 | `DirectoryIngestor.ingest_album(spec, root)` derives each subfolder's `[start_at, end_at]` from its photos' capture timestamps, behind a `SegmentSource` seam shared with the PDF path (spine refactored into `_ingest`; flat/PDF path behavior-preserving). Undated/empty folders skip with a warning (→ zero-segment album, byte-identical to flat), single-instant widens +1s, overlap caught by the shared `AlbumYAML` loader. Manifest hardened to reject `path: "."`. 4-agent review (2× correctness, style/reuse, architecture): no defects; keep the title-suggestion tuple seam (doc-title co-product predates step 33); applied DRY `_document_source` + cleanups. `poe check` green (389/6); live multi-folder ingest deferred |
| 34 | Fan-out orchestration + `generate --manifest` | step-34-fanout-cli | a2b5766 | `ingest_manifest`/`_ingest_all` + `AlbumOutcome`: ingest N albums under ONE shared `ImmichClient` with per-album isolation (ingest.py stays pipeline-free). CLI `generate --manifest [FILE]` (opt-in) fans the tree into N books, rejecting per-album flags, threading globals, running the pipeline per album with isolation, and printing a combined summary (exit non-zero only if every album failed). 4-agent review (2× correctness, style/reuse, architecture): no correctness defects; applied captioner fail-fast (inside the guard, before uploads) + `_book_path` DRY helper. As-built: narrow pipeline catch surfaces unexpected errors (deliberate). `poe check` green (404/6); live fan-out deferred |
| 35 | Docs + end-to-end genericity verification | step-35-docs-verify | 63095ea | Closes the playbook. User docs (README multi-album section, `album-definition.md`/`folder-structure.md` cross-refs, umbrella "As-built" preamble). Verification gate: a scoped token guard (`manifest.py`/`text.py`/`ingest.py` carry no trip/day/itinerary/occasion vocabulary — whole-`src/` grep infeasible) + an end-to-end test over a committed two-album fixture tree (`tests/fixtures/manifest_tree`) driving real `load_manifest`+`_ingest_all`+materialization+patched pipeline (N albums → N `album.yml`, one folder-windowed + one zero-segment → N pipeline runs). `DatingIngestClient` hoisted to `tests/doubles.py`. 2-agent review (adversarial + style/docs): caught a stray `open-questions.md` frontmatter corruption (reverted); gate sound. `poe check` green (408/6); live fan-out deferred |

### External-library ingestion playbook (complete) (see [external-library-ingestion.md](archive/external-library-ingestion.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 36 | Plumbing (config + path translation + client `/libraries` methods + Docker mount) | step-36-external-library-plumbing | abf4779 | Foundation for opt-in scan-in-place ingest, no behavior wired yet: `ImmichSettings.ingest_mode`/`external_host_root`/`external_container_root`; pure `PathMapping` (`immich/paths.py`) host↔container translation; `ImmichClient` External-Library surface (`get_current_user_id`/`get_libraries`/`create_library`/`scan_library`/`get_library_assets`) + `originalPath` on `ImmichAsset`, sharing a new `_search_metadata` helper with `get_album_assets`; opt-in commented `${PHOTOS_LOCATION}:/external:ro` mount + `.env.example`. 2-agent review (adversarial correctness + style/reuse): no blockers/majors; nits applied. `/libraries` methods modelled on documented API, respx-tested only — **live-verify shape on first reachable Immich** (live-integration gap). Static gate + all non-container tests green; container tests were blocked at merge by an unresponsive Docker daemon (env, not code), accepted by the user. **Re-verified 2026-07-19 with Docker running: the container-backed suite passes (44 passed, incl. the pgvector testcontainer).** `_wait_for_readiness` scan-queue extension deferred to step 37 |
| 37 | The `AssetRegistrar` seam (extract `UploadRegistrar`; add `ExternalLibraryRegistrar`; inject into `DirectoryIngestor`) | step-37-asset-registrar | b09f994 | Wires the step-36 plumbing behind an opt-in seam (default upload unchanged). `_ingest`'s inline `{path: upload_asset(path)}` → an injected `AssetRegistrar.register(directory, image_paths) → {host path → asset id}`; everything downstream reads only that map. `UploadRegistrar` (today's upload, extracted verbatim, default) + `ExternalLibraryRegistrar` (ensure one shared library reused by import path → scan → wait the `library` queue → map by `originalPath` through `PathMapping`, filter tree-wide library to this album, fail-fast naming unscanned photos). Readiness poll generalized to a shared `_wait_for_queues(get_jobs, queues, …)` so each strategy declares its queues (EXIF for the ingestor, scan queue for the registrar). `build_registrar` maps `ingest_mode` → registrar (validation out of `config.py`, mirrors `build_captioner`); `ingest_directory`/`ingest_manifest` build it from settings. Complexity gate split `_map_assets` (rank B → helpers). Inline review (correctness/reuse/simplification/altitude): no defects; stale "upload" doc comments refreshed. **Live-verify caveat:** async scan means the `library`-queue poll's initial idle check could race a not-yet-enqueued job on a real Immich — folded into the live-integration gap. `poe check` green (434/6, incl. pgvector testcontainer). `--ingest-mode` flag + user docs are step 38 |
| 38 | The `--ingest-mode` flag + docs (closes the playbook) | step-38-ingest-mode-flag | 55eb1ad | Exposes the step-37 seam on the CLI: `generate --ingest-mode {upload,external}`, flag > `IMMICH__INGEST_MODE` env > `upload` default (mirrors `--captioner`). Run-level (applies to every album incl. `--manifest` fan-out), so it is **not** a per-album flag and is never rejected. `INGEST_MODES` centralizes the valid set the CLI `choices` and `build_registrar` dispatch both read; `build_registrar` takes an `ingest_mode` override threaded from `ingest_directory`/`ingest_manifest`. Docs: README "Scan photos in place (no second copy)" section + ingest stage-doc note (mount, `EXTERNAL_HOST_ROOT`, admin-key, originals-must-stay-mounted trade-off). Default upload path unchanged; new CLI/precedence tests + a `--help` smoke. Inline review: no defects. `poe check` green (438/6). Live external run against a real Immich + bind mount stays in the live-integration gap (no reachable Immich here) |

### CLI unification (user-requested, 2026-07-19)

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 39 | Fold `generate`+`run` into one strategy-based `build` command | step-39-unify-cli-build | 6f3c5c1 | User-requested cleanup: one `photobook-generator build` with two switchable phases decided once args are parsed into an `AlbumSource` strategy (ABC). `build <dir>` = `DirectoryIngestSource` (ingest one + run); `--manifest` = `ManifestIngestSource` (fan-out); `--no-ingest --album <slug>` = `NoIngestSource` (skip ingest, run existing `album.yml` — the old `run`). `_select_source` picks the strategy; a single `_run_pipelines`+`_report` driver runs the pipeline over each `PreparedAlbum` and prints one combined summary — replacing the scattered `_generate*`/`_run` handlers. **Surface decisions (confirmed with user):** new verb `build`; `--no-ingest` (explicit) to skip; **clean break, no `generate`/`run` aliases**. Argument misuse (missing `DIR`, `--album` sans `--no-ingest`, per-album flags in fan-out, ingest options with `--no-ingest`) is now a uniform argparse usage error (exit 2, was 1 for per-album). `test_cli.py` rewritten; `test_genericity.py` retargeted to `_run_pipelines`/`_prepared_from_outcome`; poe `run`/`generate` tasks → `build`; docs (README.md/.html, AGENTS, working-agreement, folder-structure, ingest stage, external-library playbook) updated. Inline review (correctness/reuse/simplification/altitude): no defects; `_select_source` split (rank B → A). `poe check` green (440/6). **Follow-up (`3c1177f`):** split the single `cli.py` into an `album/cli/` package — `parser.py` (argparse), `sources.py` (`AlbumSource` strategies + `select_source`), `runner.py` (`BuildRunner` + `run_pipelines`), `__init__.py` (public `main`). Pure structural move, no behaviour change; bodies verbatim, tests retargeted patch-at-use-site, `poe check` still 440/6 |

### Per-album curation tuning (user-requested, 2026-07-20)

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 40 | Per-album `select` near-dup threshold via `album.yml` | step-40-per-album-similarity-threshold | e1ed8ea | User hit near-identical frames surviving `select` on a portrait-session album (`zdjecia-sesja-goszcz`): DB showed 118 photos → 1 scene, 0 Immich dup-groups, **64 selected**, every surviving pair at 0.85–0.899 CLIP cosine — just under the hard-wired 0.9 default, so the near-dup guard was working but too lenient for burst content. Added an opt-in `Album.similarity_threshold` (migration `f1a8b6d34c92`, nullable `Float`) + `AlbumYAML.similarity_threshold` (`Field(gt=0, le=1)`); `select` reads it via a new `_active_threshold` property that falls back to the stage default (0.9) when NULL — face-free/unset albums byte-for-byte unchanged (genericity). Set `0.82` in the session album's `album.yml`; re-running `select` alone (real pgvector) cut it **64 → 22** picks (max surviving pair 0.8196). Tests: override + NULL-fallback discriminating pairs at exactly 0.85 cosine in `test_select.py` (stubbed similarity), parse/range/absent in `test_definition.py`, **plus a real-pgvector integration test** (`test_album_similarity_threshold_honored_by_real_cosine_query`) proving the album's 0.82 — not the 0.9 constructor default — drives the actual cosine query on real 768-d vectors in a migrated Postgres container. Also manually verified on the live DB: re-running `select` alone cut album 3 **64 → 22** picks (max surviving pair 0.8196). **Note:** `get_or_create_album` doesn't re-import `album.yml` for an existing slug, so the existing row was patched directly. 2-agent review (adversarial correctness + style/architecture): no defects. `poe check` green (448/6) |

### Failure escalation + logging (user-requested, 2026-07-20)

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 41 | Escalate total-failure stages + stage/progress logging | step-41-failure-escalation-logging | f04b4fc | User asked why a stage that hit an error still showed "done". Root cause: the four isolating stages (`quality`/`enhance`/`render`/`caption`) catch every per-photo exception, count it, and never re-raise, so `Stage.run()` always reached its unconditional commit — a *systemic* failure (Immich down, `darktable-cli` missing, model unavailable) where every attempted photo failed still reported success. **Fix:** `Stage._failure_counts(result) -> (failed, attempted) \| None` hook on the base (default `None` ⇒ `sync`/`cluster`/`select`/`assemble`/`fulfill` untouched); `Stage.run()` raises **`StageTotalFailure`** (a `RuntimeError` subclass) iff `attempted > 0 and failed == attempted`. `attempted` excludes photos short-circuited before any work (`skipped`/`unclustered`/`not_ready`) so one bad photo among many never trips it. **caption caveat (caught in review):** in the DAG `caption` runs after `enhance`, which already analyzed clustered photos via the shared `PhotoAnalyzer`, so caption's `skipped` = "captioned elsewhere", not a short-circuit — caption counts `skipped` toward non-failure (`captioned + failed + skipped`), else one failing unclustered photo escalated a 99%-captioned album. **Isolation (caught in review):** `run_pipelines` now catches `StageTotalFailure` so a systemic per-album failure is isolated (fan-out siblings still build) instead of aborting the whole build with a traceback. **Logging:** hoisted stage start/done INFO logging onto `Stage.run()` (logs the result `repr`) + a time-throttled `ProgressLogger` (stdlib logging, injected clock, 30s) in the four per-photo loops; **`print()` → logging:** the 4 `runner.py` prints route through a dedicated non-propagating `album.cli.output` logger (INFO→stdout, WARNING+→stderr, no prefix, streams resolved at emit time so `capsys` still captures — same output contract, now verbosity-controllable). `scripts/build_cat_album.py` left as `print()` (out of scope). Tests: 4 "all fail → raises" (one per stage) + base escalation/logging (`caplog`) + `ProgressLogger` throttle + caption false-positive regression + `run_pipelines` per-album isolation. 4-agent review (2 adversarial correctness, style/reuse, architecture) + fix re-review: caught the caption false-positive + the isolation gap, both fixed. `poe check` green (465/6, incl. Docker pgvector). Live systemic-failure re-verify deferred (no reachable Immich) |

### Caption tuning (user-requested, 2026-07-21)

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 42 | Caption one-sentence summary + configurable output language | step-42-caption-language-summary | 504a5e6 | User: the `ClaudeCaptioner` ran too verbose, and wanted the output language selectable (default Polish). Its system prompt now demands **exactly ONE short summary sentence** ("not a description; no lists, no line breaks, no preamble") instead of "a short, natural caption". Output language is now an argument: `ClaudeCaptioner(language=…)` (default `DEFAULT_CAPTION_LANGUAGE = "Polish"`) builds a per-instance agent from a language-specialised `_caption_system_prompt(language)` — one language per run. Threaded end to end: `Settings.caption_language` (flat, env `CAPTION_LANGUAGE`, default "Polish", mirrors `captioner`); `build_captioner(name, *, language=None)` + language-aware registry factories (the offline `TemplateCaptioner` composes from album metadata and ignores it); `from_settings(*, language=None)` = flag > `CAPTION_LANGUAGE` env > "Polish"; `--caption-language LANGUAGE` on `build`, threaded via `_resolve_captioner`. New `capturing_caption_system_prompt` double + tests (default-Polish, configurable language, one-sentence enforcement, `--caption-language` threads through `build_captioner`). Modest self-contained diff → inline adversarial + style review (no fan-out per the workflow's scale-down): no defects; `captioner.py`→`config.py` import is one-directional (config never imports the captioner registry) and already transitively present via `agents/_model`. `poe check` green (469 passed, 6 skipped, incl. Docker pgvector; a stale `testcontainers-ryuk` name-conflict on the first run was cleared and the container suite re-run clean). Live-Claude Polish caption spot-check deferred (no `ANTHROPIC_API_KEY` / reachable service) — folds into the live-integration gap |

### PDF rendering on demand (user-requested, 2026-07-21)

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 43 | Render the master PDF on demand in `LuluPrinter`, not on every run | step-43-pdf-on-demand | 9663d98 | User asked why the print-ready PDF was generated on every run when the default output is a browsable webpage. Root cause: `assemble` unconditionally rendered the master PDF via WeasyPrint, but only an opt-in Lulu order reads `assembled_pdf_path` — the default `HtmlPrinter` renders from the on-disk photos and never touches it. **Change:** `assemble` is now **layout-only** — it stamps each placed photo's `page_number` (the signal `printers/base.py:iter_book_pages` reads) + computes the Lulu spine width; it renders no file, drops the `output_dir`/`templates_dir` constructor args and `AssembleResult.output_path`, and its idempotency now keys off "photos already placed" (any `page_number` set) instead of a written PDF path. `LuluPrinter` renders the PDF on demand from `iter_book_pages` (Jinja2 + WeasyPrint; it rebuilds assemble's dedicated section pages from `iter_book_pages`'s `starts_segment`, so the PDF page count still matches assemble's `pages`/spine width), writes `<output_dir>/<slug>/assembled.pdf`, records `Album.assembled_pdf_path`, and gates Lulu submission on a new injectable `publish_pdf` hook (local path → public URL) — default `None` keeps the known hosting gap, refusing with `UnreachablePdfError` *after* rendering. Net: the default `HtmlPrinter` path never invokes WeasyPrint and `assembled_pdf_path` stays NULL there. Print page geometry (trim/bleed/page-box constants) moved to `printers/lulu.py`; the spine formula stays in `assemble`. Tests: rewrote `test_assemble.py` (layout/`page_number` only, no PDF), `test_lulu_printer.py` (renders real PDFs — asserts trim+bleed page box + section-page structure + the `publish_pdf`/`UnreachablePdfError` seam), and fixed the `flow_integration` assertion (a default run leaves `assembled_pdf_path` NULL). Updated the `assemble`/`fulfill`/`index`/`pipeline-execution` docs + the `build_cat_album` demo. Adversarial + style **self-review** (the multi-agent fork panel was unavailable inside the worktree harness): no defects — verified the assemble↔`LuluPrinter` page-number alignment and removed a dead `_log`. `poe check` green (469 passed, 6 skipped, incl. Docker pgvector). Live Lulu order still gapped on PDF hosting (`UnreachablePdfError`) — folds into the live-integration gap |

## Notes / blockers

**Operational status.** The pipeline is **fully offline by default** (classical tone +
template caption, no API/key); Claude is opt-in per run (`--captioner claude`, `--force`
for A/B). Under `--force`, `enhance` and `caption` each re-run the shared analysis for a
clustered photo — an accepted ~2× cost on paid Claude A/B; prefer a fresh slug for a large
online run. Build completeness is summarized under [Outstanding](#outstanding-todo).

**Process conventions live in the workflow docs, not here.** The review panel, the
waived human-review gate, and the `main`-commit-protection rule are in
[implementation-workflow.md](../process/implementation-workflow.md); the 2026-07-14
parallel-build exception is recorded in [log.md](../log.md).

**Open blockers — all "no reachable live service in this sandbox"; nothing blocked on code:**
- **Live-integration gap.** No reachable Immich, `ANTHROPIC_API_KEY`, host `darktable-cli` (only in
  the app image), or Lulu sandbox creds. A full run verified against real Postgres+Prefect but fails
  cleanly at `sync` (`ConnectionRefused`). Deferred to first live run: honeymoon + wedding re-verify,
  faces/rating/geocode on real assets, live-Claude caption spot-check (9/10/12/19/28/29), a real
  darktable render, and a single-copy Lulu test order (+ confirm `GET /print-jobs/{id}/` schema).
  Also folds in the two still-open [design questions](open-questions.md): **#2** confirm the
  honeymoon originals genuinely lack GPS (if some carry it, `cluster`'s spatial path gets exercised on
  the first album) and **#3** per-photo capture-zone reliability on real assets (Immich's
  `exifInfo.timeZone`/`OffsetTimeOriginal` vs the `Album.timezone` fallback, incl. the mid-segment
  zone-span edge). Also pending live: confirm the wired LAION scorer (step 31) picks the visibly
  better frame within a scene on a real album. Step 24's offline ML stack already closed the faces +
  CLIP half.
- **No PDF-hosting for `LuluPrinter` (step 15).** `Album.assembled_pdf_path` is a local path; Lulu's
  `source_url` needs a fetchable `http(s)://` URL, so `LuluPrinter.print` raises `UnreachablePdfError`.
  Only affects the opt-in `LuluPrinter` (default `HtmlPrinter` needs none).

Coding landmines (e.g. the ruff py314 `except`-paren formatter bug) live in the
[working agreement](../process/working-agreement.md#pre-commit-hooks-local-enforcement); resolved
ones are in [log.md](../log.md).

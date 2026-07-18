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
Update this file whenever a step changes state. Steps are delivered strictly in
order, one at a time.

**Status values:** `todo` → `in-progress` → `agent-review` → `awaiting-user` →
`done`. Use `blocked` (with a note) if a step cannot proceed.

This tracker is split into two sections — **[Outstanding (TODO)](#outstanding-todo)**
and **[Completed (DONE)](#completed-done)**. **Bookkeeping rule:** a step lives in
*Outstanding* while it is `todo`/`in-progress`/`blocked` (set `in-progress` and
record its branch when work begins); the moment it merges to `main` as `done`,
**move its row out of Outstanding into the matching Completed sub-table**, with its
commit hash. So *Outstanding* always shows exactly what is left, and *Completed* is
the audit trail. Keep this split accurate — it is part of the per-step
[implementation workflow](/process/implementation-workflow.md), not optional tidiness.

## Outstanding (TODO)

The implementing agent takes the **lowest-numbered** step here. The remaining item
is optional/deferred — nothing is blocked on missing code (see
[Notes / blockers](#notes--blockers)).

| # | Step | Playbook | Status | Branch | Notes |
|---|------|----------|--------|--------|-------|
| 23 | DB-backed flow smoke test (optional) | testcontainers | todo | — | whole flow on real Postgres, Immich/Claude/Lulu as doubles — largely subsumed by step 25's `test_flow_integration.py` (real `pipeline()` on a pgvector container, every external boundary doubled); revisit only if a gap remains |

## Completed (DONE)

### Build order

| # | Step | Branch | Status | Commit | Notes |
|---|------|--------|--------|--------|-------|
| 0 | Scaffold | step-00-scaffold (merged, deleted) | done | 11d290f | `config.py`/`definition.py`/`flow.py` deliberately not created — deferred to steps 3 and 14 |
| 1 | Containers | step-01-containers (merged, deleted) | done | 240fbe0 | vendored Immich's stack as its own file, pulled in via Compose `include:` |
| 2 | Data model + migrations | step-02-data-model (merged, deleted) | done | 68ed96f | |
| 3 | Album definition loading | step-03-album-definition (merged, deleted) | done | 84260ef | |
| 4 | Immich client | step-04-05-immich-client-sync (merged, deleted) | done | 8172dfa | delivered with step 5 (one branch/review) per user; resolves Open Q1 (no CLIP embeddings); live fixture recording skipped — see log |
| 5 | `sync` stage | step-04-05-immich-client-sync (merged, deleted) | done | 8172dfa | delivered with step 4; live "run for real" skipped (no reachable Immich stack) per user |
| 6 | `quality` stage | step-06-quality (merged, deleted) | done | a6c4b9e | real OpenCV blur; aesthetic scorer + CLIP embedder injected (protocols) — real LAION/open_clip impls deferred (heavy) |
| 7 | `cluster` stage | step-07-cluster (merged, deleted) | done | 2a39df5 | temporal + spatial(DBSCAN) scenes; no-segments & no-GPS fallbacks tested (genericity guard) |
| 8 | `select` stage (MMR) + `db/similarity.py` | step-08-select (merged, deleted) | done | 4fec4a1 | real pgvector query verified against a Postgres testcontainer |
| 9 | Agent (`analysis.py`) | step-09-agent (merged, deleted) | done | 38a4057 | real-Claude sanity check deferred (no `ANTHROPIC_API_KEY`); tested via `TestModel` only |
| 10 | `enhance` stage | step-10-enhance (merged, deleted) | done | e7fccfe | shared idempotent `ensure_analysis()` trigger (reused by step 12 `caption`); cross-camera exposure baseline via `skimage.exposure.match_histograms`; darktable-native XMP sidecars under `data/albums/<slug>/xmp/` |
| 11 | `render` stage | step-11-render (merged, deleted) | done | 168940f | adds `ImmichClient.get_original()` (originals weren't fetchable before — only thumbnails); manual "visibly corrected JPEG" check deferred (no `darktable-cli` on this host, only in the Docker app image) |
| 12 | `caption` stage | step-12-caption (merged, deleted) | done | 69f82b3 | thin wrapper over `enhance`'s `ensure_analysis()`; order-independent with `enhance` (whichever runs first triggers the one shared call); manual ~20-caption spot-check deferred (no `ANTHROPIC_API_KEY`) |
| 13 | `assemble` stage | step-13-assemble (merged, deleted) | done | 9ec5ca7 | resolves Open Q4 (Lulu 8.5x8.5in square trim, 0.125in bleed, spine = pages/444+0.06in); adds `Album.assembled_pdf_path` (own migration) — the contract step 15 reads |
| 14 | Wire the flow | step-14-wire-flow (merged, deleted) | done | ada27d6 | real Postgres+Prefect verified (migrations applied, a real flow run tracked and visible via the API); fails cleanly at `sync` — no reachable Immich instance, open since steps 4-5; real LAION/open_clip scorer still deferred (step 6) |
| 15 | `fulfill` stage | step-15-fulfill (merged, deleted) | done | 92a7d4c | Lulu Print API client (OAuth2 client-credentials); reads `Album.assembled_pdf_path`, wrote `Album.lulu_order_id` (renamed to `output_ref` post-step-16 when `fulfill` became printer-pluggable — see Notes); real Lulu sandbox order deferred (no credentials); no PDF-hosting/upload mechanism yet — see notes below |
| 16 | Prove genericity | step-16-prove-genericity (merged, deleted) | done | d7e903d | adds `data/albums/smith-wedding/album.yml` (wedding-shaped: hour-scale segments, single day) + `tests/test_genericity.py`; zero `src/` files touched — confirmed the abstraction; live run hits the same no-reachable-Immich boundary as step 14 |

### Enrichment playbook (post-build-order — see [enrichment.md](/build/enrichment.md))

| # | Step | Branch | Status | Commit | Notes |
|---|------|--------|--------|--------|-------|
| 17 | Faces in client + `sync` (+schema) | step-17-faces-sync (merged, deleted) | done | c99d319 | `ImmichFace` + `get_faces` (`GET /faces?id=`); `Photo.face_count`/`person_ids` + album-scoped `Person` table (migration `c3d9e5a1f742`, verified on real Postgres, zero autogenerate drift); per-asset N+1 face fetch, idempotent Person upsert, failure-isolated for both httpx + pydantic ValidationError; genericity guard holds (`person_ids=[]` ⇒ face-free behavior). 3-agent review; 177 tests green |
| 17b | Lead-face resolution | step-17b-lead-faces (merged, deleted) | done | 06810d7 | `LeadFacesYAML` (exactly one of reference_image/reference_asset_id/person_ids) + `load_lead_faces`; client `upload_asset` (POST /assets); `Album.lead_reference_asset_id` cache (migration `d4a1c7b93e56`, verified on Postgres); `LeadFaceResolver` uploads + polls async face detection, marks `Person.is_lead` (exact set, clears stale); `_resolve_leads_task` after sync; genericity guard holds. 3-agent review (fixed stale-lead clearing); 194 tests |
| 18 | People-aware + lead-aware `select` | step-18-people-select (merged, deleted) | done | f3afc54 | effective-score boosts (group_boost/person + lead_boost/lead, tunable) + lead guaranteed coverage (bypasses near-dup guard vs non-leads; couple each covered) + strict-on-non-leads (no generic coverage phase — chosen posture); byte-for-byte for face-free albums. 3-agent review (removed a provably-dead generic-coverage phase); 205 tests |
| 19 | Name-grounded `caption` (optional) | step-19-name-caption (merged, deleted) | done | cd77da6 | `PhotoContext.person_names` + pure `_resolve_person_names` (photo `person_ids` → `Person.name` via the album-scoped `photo.album.people` cache — one query/album from the identity map, order preserved, unnamed/unknown dropped); `format_context` emits a `People in this photo:` line only when non-empty; rides the **one** shared analysis call (no 2nd round-trip); byte-for-byte for a face-free or all-unnamed photo (genericity guard). 2 adversarial reviews (correctness clean; style/reuse findings applied — shared `doubles.canned_analysis_response`, capturing double → class fixture). 223 tests (+5). Live-Claude caption spot-check still deferred (no `ANTHROPIC_API_KEY`). |
| 20 | Rating + geocoded place (optional; OCR deferred) | worktree-step-20-rating-place (merged, deleted) | done | bb430d2 | `Photo.rating` → a soft per-star `select` boost (`rating_boost` 0.02/star; starred up, rejected down, reorders comparable photos only, never overrides a clearly better shot, lead coverage untouched); `Photo.city/state/country` → a `caption` `Location:` grounding line via `PhotoAnalyzer._format_place` (joined most-to-least specific, deduped); **`cluster` left untouched** (keeps GPS/DBSCAN — no clustering on place names). Both come off data `sync` already fetches — **no extra Immich call**. **OCR deferred** (a 2nd per-asset N+1 for a playbook-rated "minor" signal, absent from the pinned v3.0.2 snapshot — full rationale in [enrichment.md](/build/enrichment.md) step 20). One migration (`88a302b1efdf`, autogenerated → verified zero drift + clean downgrade/upgrade round-trip on a real pgvector container). 2 adversarial reviews (correctness: no defects; cleanup: `capturing_model` promoted to a file-level fixture + `dict.fromkeys` idiom applied). 290 tests (+13, all TDD red-first). Genericity guard holds — an unrated, un-geocoded album is byte-for-byte as before. Live-Immich re-verify of the rating/geocoding fields deferred (same no-reachable-Immich boundary as steps 4-5). |

*(Enrichment playbook complete — steps 17, 17b, 18, 19, 20 all done.)*

### TestContainers playbook (see [testcontainers.md](/build/testcontainers.md))

| # | Step | Branch | Status | Commit | Notes |
|---|------|--------|--------|--------|-------|
| 21 | pgvector schema + integration harness | step-21-pgvector-integration (merged, deleted) | done | ac2cd14 | alembic-migrated pgvector container fixture; `select` over the real `PgVectorSimilarity` (near-dup skip, idempotent; lead coverage with a negative control + `person_ids` JSON round-trip); migrations reach head + no drift; migration round-trip; `integration` marker + `poe test-fast`. 2-agent review (fixed a fileConfig/caplog cross-test leak). 211 tests |
| 22 | darktable-cli render integration | step-22-darktable-render (merged, deleted) | done | 8aac81b | real `DarktableRenderer` inside the shipped app image renders a real `write_xmp_sidecar` sidecar → asserts output JPEG exists, decodes, **visibly brighter** (`tests/test_render_integration.py`). Validated the darktable XMP history-stack format — see [enhance.md](/stages/enhance.md). Closes step-11's deferred manual check. Shells to `docker` CLI (docker-py build breaks on the py3.14 gcloud cred-helper landmine). |
| 24 | Immich ML integration (pre-baked offline model image) | step-24-immich-ml-integration (merged, deleted) | done | ad86550 | user-directed, out of strict order (23 still todo). Pre-baked ML image (CLIP `ViT-B-32__openai` + faces `buffalo_l`, CPU ONNX, `HF_HUB_OFFLINE=1`) mirroring `ModelBase._download`'s layout; real 4-service stack (server + baked ML + vectorchord Postgres + valkey) driven via testcontainers `DockerCompose`; uploads `skimage.data.astronaut()` through the real `ImmichClient` and asserts a real ML face (`get_faces`, closes step 17), the real `LeadFaceResolver` reference_image upload+async-detection poll to an ML-detected face (closes 17b timing), and smart search returning the asset (CLIP embedded offline, open Q1). New `ImmichClient.smart_search`. Opt-in (`IMMICH_ML_INTEGRATION=1`/`poe test-immich-ml`), skipped in `poe check`. `poe bake-immich-ml` (bake + pre-pull) is the one internet-touching step; runs are offline after. 2 adversarial Opus reviews; all findings applied. **Follow-up (not blocking):** baked image ~3.1 GB, base-ML-dominated (~2.3 GB; models only ~0.77 GB) — slimming deferred. 290 pass/3 skip + 3 live pass |
| 25 | Parallelize the flow (fan-out groups) | step-25-parallelize-flow (merged, deleted) | done | 036fcc2 | DAG execution per `pipeline-execution.md`: 4 sequential stages keep the shared session; 6 grouped stages (`resolve-leads`/`quality`/`cluster`/`enhance`/`render`/`caption`) take a `session_factory` and open their own pooled connection via a new `_owns_session` decorator (+ sibling `_autocommit` for the 4 sequential tasks); two `submit()`/`result()` fences — group 1 (`resolve-leads`‖`quality`‖`cluster`, join at `select`) and group 2 (`enhance`, then `render`‖`caption`, join at `assemble`). Commit-before-fanout/`expire_all()`-after-join at both fences. Review caught a real bug in the original design sketch: `enhance`‖`caption` running fully concurrently raced on `PhotoAnalyzer.ensure`'s shared idempotency columns (double vision-model call + conflicting-write race) — fixed by joining `enhance`'s future before `render`/`caption` are even submitted. New `tests/album/test_flow_integration.py`: real concurrent per-task sessions against a Postgres+pgvector testcontainer, external boundaries doubled, incl. a counting-model regression guard for the caught bug. 3-agent Opus review (correctness/cleanup/tests) + a re-review after the fix; 218 tests. Live `photobook-generator run` end-to-end stays deferred (no reachable Immich/Claude/darktable/Lulu), same standing note as every prior step. |

*(Step 23 is outstanding — see [Outstanding (TODO)](#outstanding-todo).)*

### Single-command ingestion playbook (see [single-command-ingestion.md](/build/single-command-ingestion.md))

| # | Step | Branch | Status | Commit | Notes |
|---|------|--------|--------|--------|-------|
| 26 | Immich album creation in the client (`create_album` + `get_jobs`) | step-26-immich-album-create (merged, deleted) | done | 94fba06 | playbook step 1 — `create_album` (`POST /albums`, returns id) + `get_jobs` (`GET /jobs` → `dict[str, ImmichJobCounts]`, the readiness signal for step 27); lean boundary models (jobCounts flattened), respx fixtures. Modelled against the OpenAPI contract, not a live capture (same deferral as steps 4-5) — re-verify on first live ingestion. 2 adversarial reviews (correctness clean; cleanup/reuse applied) + a focused re-review; 241 tests green |
| 27 | `ingest.py` + `photobook-generator generate` subcommand (Feature A) | step-27-ingest-cli (merged, deleted) | done | c1c5146 | playbook step 2 — `DirectoryIngestor` (dir walk → upload → `create_album` → `get_jobs` readiness poll → materialize `album.yml`) then the UNCHANGED `pipeline`. CLI consolidated to a single `photobook-generator` command with `generate`/`run` subcommands (user-directed; replaced the `album` script). `--force` re-runs an existing slug as is (no re-upload/orphan/clobber — review caught the earlier re-materialize design as data-loss+orphan footguns). 2 adversarial reviews + a focused re-review; 264 tests. Live ingestion deferred (same no-reachable-Immich boundary as steps 4-5/26) |
| 28 | `agents/segments.py` + `--source-document` (Feature B) | step-28-segments-agent (merged, deleted) | done | 1e2b0af | playbook step 3 — PydanticAI `extract_segments`/`extract_segments_from_pdf` (pypdf text + scanned-image fallback; reuses `SegmentYAML` + optional title), wired into `generate --source-document`: extracts segments grounded on the photo capture range, writes them to `album.yml`, validated fail-fast (`_validate_materialized`). Occasion-agnostic (genericity PASS in review). Shared `agents/_model.py` extracted. 2 adversarial reviews + a focused re-review; 277 tests. Live-Claude extraction spot-check deferred (no `ANTHROPIC_API_KEY`), same as steps 9/10/12; scanned-PDF path exercised via weasyprint fixtures |

### Offline analysis playbook (see [local-analysis.md](/build/local-analysis.md))

| # | Step | Branch | Status | Commit | Notes |
|---|------|--------|--------|--------|-------|
| 29 | Offline analysis — classical replacement for the Claude vision call | step-29-offline-analysis (merged, deleted) | done | ba6c1bf | Dropped the pipeline's **last external-API dependency**: the fused per-photo vision call is split into two injectable collaborators in a new `album.analysis` package — a `ClassicalToneEstimator` (CV: auto-exposure to a target luma, gray-world AWB, histogram-percentile shadows/highlights; tone is now **always classical**) and a `Captioner` seam (`TemplateCaptioner` offline default + `ClaudeCaptioner` opt-in), composed by `PhotoAnalyzer`. Captioner is **selectable per run** (`CAPTIONER` env / `--captioner` CLI, + `--force` on `run` for A/B), so a default `photobook-generator run` needs **no external API and no key**. Retired `agents/analysis.py` (`AnalysisResult`/`analyze_photo`); moved `ToneParams` → `album.analysis.tone`; single-sourced the image decode, `clamp`, and tone bounds. **4-agent review** (2 adversarial correctness, style/reuse, architecture — the new panel, see [implementation-workflow.md](/process/implementation-workflow.md) §6): fixes applied — `--force` threads to `caption` (re-captions the unclustered selected photos `enhance` skips), non-empty caption guard, force-wiring test. Genericity guard holds (honeymoon + wedding). `uv run poe check` green (323 pass / 3 skip, incl. Docker integration). Live-Claude caption spot-check deferred (no `ANTHROPIC_API_KEY`), same as steps 9/10/12/19/28. |

## Notes / blockers

**Current status.** Every build-order + enrichment + testcontainers + single-command-ingestion
+ offline-analysis step is done and merged; only the optional **step 23** remains (largely
subsumed by step 25's `test_flow_integration.py`). The pipeline is **fully offline by default**
(classical tone + template caption, no API/key); Claude is opt-in per run (`--captioner claude`,
`--force` for A/B). Under `--force`, `enhance` and `caption` each re-run the shared analysis for a
clustered photo — an accepted ~2× cost on paid Claude A/B (cheap offline); prefer a fresh slug for
a large online A/B.

**Standing process notes.**
- **Human-review gate waived (step 4 onward, per user):** after `poe check` green + sub-agent
  review clean, squash and fast-forward-merge autonomously.
- **Sub-agent review panel is up to 4 (2026-07-17):** 2 adversarial correctness + style/reuse +
  architecture — see [implementation-workflow.md](/process/implementation-workflow.md) §6. Scale down for small diffs.
- **`main` is commit-protected:** do the tracker + `log.md` update on the step branch *before* the
  fast-forward merge.
- **Parallel-build precedent (2026-07-14, user-approved exception):** steps 10/11/12/13/15 were built
  concurrently on separate worktrees after a dependency check showed no shared files; a coordinator
  reconciled the two independent migrations + docs. Not a new default.

**Open blockers — all "no reachable live service in this sandbox"; nothing blocked on code:**
- **Live-integration gap.** No reachable Immich instance, `ANTHROPIC_API_KEY`, `darktable-cli` on the
  host (only in the app image), or Lulu sandbox creds. A full `photobook-generator run` was verified
  against real Postgres+Prefect but fails cleanly at `sync` (`ConnectionRefused`). Deferred to first
  live run: honeymoon + wedding re-verify, faces/rating/geocode fields on real assets, live-Claude
  caption spot-check (steps 9/10/12/19/28/29), a real darktable render against live originals, and a
  single-copy Lulu test order (+ confirm `GET /print-jobs/{id}/` schema). Step 24's offline ML stack
  already closed the faces + CLIP half against a real (pre-baked) Immich — see
  [testcontainers.md](/build/testcontainers.md).
- **No PDF-hosting for `LuluPrinter` (step 15).** `Album.assembled_pdf_path` is a local path; Lulu's
  `source_url` needs a fetchable `http(s)://` URL, so `LuluPrinter.print` raises `UnreachablePdfError`.
  Needs real hosting wired in — only for the opt-in `LuluPrinter` (the default `HtmlPrinter` needs none).
- **Real LAION/open_clip scorer not wired (step 6, resurfaced step 14).** `flow/_flow.py`'s
  `_UnavailableQualityModel` is a raising placeholder → photos land with
  `blur_score`/`aesthetic_score`/`clip_embedding` NULL; `select` has a tested fallback, so a run
  completes with degraded curation. Wiring the real predictor (`open_clip` + `torch` + weights, heavy)
  is follow-up.

**Toolchain landmine.** `ruff` 0.15.21's formatter under `target-version = "py314"` rewrites
`except (A, B):` into invalid paren-less syntax — use separate `except` clauses or `# fmt: skip`, and
re-run `poe check` immediately before every commit. See [log](/log.md) 2026-07-13.

**Resolved landmines (kept as record).** Ryuk reaper socket-mount failure went away with a Docker
reinstall (2026-07-15) — plain `poe check` is green, no `TESTCONTAINERS_RYUK_DISABLED` needed;
`testcontainers`' `PostgresContainer` defaults to a psycopg2 URL, so pass `driver="psycopg"` (this
project uses psycopg3). The printer abstraction / `Album.output_ref` rename (2026-07-14) and the
`db/queries.py` hoist (open Q7) are done — see the DONE tables and
[open-questions.md](/build/open-questions.md).

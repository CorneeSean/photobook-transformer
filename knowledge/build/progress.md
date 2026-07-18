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
| 30 | CLI flag for multiple lead-face reference photos | [step-30-cli-lead-faces.md](/build/step-30-cli-lead-faces.md) | todo | step-30-cli-lead-faces | `reference_images` on `lead_faces` + a migration generalizing `Album.lead_reference_asset_id` to a list + a repeatable `--lead-face` flag on `generate`/`run` |

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

### Enrichment playbook (see [enrichment.md](/build/enrichment.md))

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

### Single-command ingestion playbook (see [single-command-ingestion.md](/build/single-command-ingestion.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 26 | Immich album creation in the client | step-26-immich-album-create | 94fba06 | `create_album` + `get_jobs` (step 27's readiness signal); OpenAPI-modelled — re-verify live |
| 27 | `ingest.py` + `generate` subcommand (Feature A) | step-27-ingest-cli | c1c5146 | `DirectoryIngestor` → the unchanged `pipeline`; single `photobook-generator` CLI; `--force` re-runs a slug safely; live ingestion deferred |
| 28 | `agents/segments.py` + `--source-document` (Feature B) | step-28-segments-agent | 1e2b0af | PydanticAI segment extraction (pypdf + scanned fallback); occasion-agnostic; validated fail-fast; extraction spot-check deferred |

### Offline analysis playbook (see [local-analysis.md](/build/local-analysis.md))

| # | Step | Branch | Commit | Notes |
|---|------|--------|--------|-------|
| 29 | Classical replacement for the Claude vision call | step-29-offline-analysis | ba6c1bf | dropped the **last external-API dependency** — `ClassicalToneEstimator` + `TemplateCaptioner` default, `ClaudeCaptioner` opt-in (`--captioner`); retired `agents/analysis.py`; 4-agent review; caption spot-check deferred |

## Notes / blockers

**Current status.** Every build-order + enrichment + testcontainers + single-command-ingestion
+ offline-analysis step is done and merged; only the optional **step 23** remains (largely
subsumed by step 25's `test_flow_integration.py`). The pipeline is **fully offline by default**
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
- **Real LAION/open_clip scorer not wired (step 6, resurfaced step 14).** `_UnavailableQualityModel`
  is a raising placeholder → photos land with NULL scores; `select` has a tested fallback, so a run
  completes with degraded curation. Wiring the real predictor (heavy: `open_clip` + `torch` + weights)
  is follow-up.

**Toolchain landmine.** `ruff` 0.15.21's formatter under `target-version = "py314"` rewrites
`except (A, B):` into invalid paren-less syntax — use separate `except` clauses or `# fmt: skip`.
See [log.md](/log.md) 2026-07-13.

**Resolved landmines (kept as record).** Ryuk socket-mount failure went away with a Docker reinstall
(2026-07-15) — plain `poe check` is green; `PostgresContainer` still needs `driver="psycopg"` (psycopg3).
The `output_ref` rename (2026-07-14) and the `db/queries.py` hoist (Open Q7) are done — see the DONE
tables and [open-questions.md](/build/open-questions.md).

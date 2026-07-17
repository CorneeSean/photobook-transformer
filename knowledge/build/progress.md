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

The implementing agent takes the **lowest-numbered** step here. Both below are
optional/deferred — nothing is blocked on missing code (see
[Notes / blockers](#notes--blockers)).

| # | Step | Playbook | Status | Branch | Notes |
|---|------|----------|--------|--------|-------|
| 23 | DB-backed flow smoke test (optional) | testcontainers | todo | — | whole flow on real Postgres, Immich/Claude/Lulu as doubles — largely subsumed by step 25's `test_flow_integration.py` (real `pipeline()` on a pgvector container, every external boundary doubled); revisit only if a gap remains |
| 24 | Immich ML integration (deferred optional) | testcontainers | todo | — | needs internet CLIP/face models or a pre-baked model-cache image — see the playbook |

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
| 25 | Parallelize the flow (fan-out groups) | step-25-parallelize-flow (merged, deleted) | done | 036fcc2 | DAG execution per `pipeline-execution.md`: 4 sequential stages keep the shared session; 6 grouped stages (`resolve-leads`/`quality`/`cluster`/`enhance`/`render`/`caption`) take a `session_factory` and open their own pooled connection via a new `_owns_session` decorator (+ sibling `_autocommit` for the 4 sequential tasks); two `submit()`/`result()` fences — group 1 (`resolve-leads`‖`quality`‖`cluster`, join at `select`) and group 2 (`enhance`, then `render`‖`caption`, join at `assemble`). Commit-before-fanout/`expire_all()`-after-join at both fences. Review caught a real bug in the original design sketch: `enhance`‖`caption` running fully concurrently raced on `PhotoAnalyzer.ensure`'s shared idempotency columns (double vision-model call + conflicting-write race) — fixed by joining `enhance`'s future before `render`/`caption` are even submitted. New `tests/album/test_flow_integration.py`: real concurrent per-task sessions against a Postgres+pgvector testcontainer, external boundaries doubled, incl. a counting-model regression guard for the caught bug. 3-agent Opus review (correctness/cleanup/tests) + a re-review after the fix; 218 tests. Live `photobook-generator run` end-to-end stays deferred (no reachable Immich/Claude/darktable/Lulu), same standing note as every prior step. |

*(Steps 23-24 are outstanding — see [Outstanding (TODO)](#outstanding-todo).)*

### Single-command ingestion playbook (see [single-command-ingestion.md](/build/single-command-ingestion.md))

| # | Step | Branch | Status | Commit | Notes |
|---|------|--------|--------|--------|-------|
| 26 | Immich album creation in the client (`create_album` + `get_jobs`) | step-26-immich-album-create (merged, deleted) | done | 94fba06 | playbook step 1 — `create_album` (`POST /albums`, returns id) + `get_jobs` (`GET /jobs` → `dict[str, ImmichJobCounts]`, the readiness signal for step 27); lean boundary models (jobCounts flattened), respx fixtures. Modelled against the OpenAPI contract, not a live capture (same deferral as steps 4-5) — re-verify on first live ingestion. 2 adversarial reviews (correctness clean; cleanup/reuse applied) + a focused re-review; 241 tests green |
| 27 | `ingest.py` + `photobook-generator generate` subcommand (Feature A) | step-27-ingest-cli (merged, deleted) | done | c1c5146 | playbook step 2 — `DirectoryIngestor` (dir walk → upload → `create_album` → `get_jobs` readiness poll → materialize `album.yml`) then the UNCHANGED `pipeline`. CLI consolidated to a single `photobook-generator` command with `generate`/`run` subcommands (user-directed; replaced the `album` script). `--force` re-runs an existing slug as is (no re-upload/orphan/clobber — review caught the earlier re-materialize design as data-loss+orphan footguns). 2 adversarial reviews + a focused re-review; 264 tests. Live ingestion deferred (same no-reachable-Immich boundary as steps 4-5/26) |
| 28 | `agents/segments.py` + `--source-document` (Feature B) | step-28-segments-agent (merged, deleted) | done | 1e2b0af | playbook step 3 — PydanticAI `extract_segments`/`extract_segments_from_pdf` (pypdf text + scanned-image fallback; reuses `SegmentYAML` + optional title), wired into `generate --source-document`: extracts segments grounded on the photo capture range, writes them to `album.yml`, validated fail-fast (`_validate_materialized`). Occasion-agnostic (genericity PASS in review). Shared `agents/_model.py` extracted. 2 adversarial reviews + a focused re-review; 277 tests. Live-Claude extraction spot-check deferred (no `ANTHROPIC_API_KEY`), same as steps 9/10/12; scanned-PDF path exercised via weasyprint fixtures |

## Notes / blockers

- **Enrichment step 20 done — rating + geocoded place; OCR deferred; enrichment
  playbook complete (2026-07-17):** `sync` now copies two optional signals off the
  data it already fetches (no extra Immich call): the manual star `rating`
  (`Photo.rating`) and reverse-geocoded place (`Photo.city/state/country`).
  `select` uses `rating` as a soft per-star boost (`rating_boost`, honoring user
  curation without overriding a clearly better shot; lead coverage unaffected);
  `caption` grounds on the place via a `Location:` line (`PhotoAnalyzer._format_place`).
  `cluster` was deliberately **not** changed — it keeps its GPS/DBSCAN logic; place
  is caption-only. All columns nullable ⇒ an unrated, un-geocoded album is
  byte-for-byte as before (genericity guard). **OCR (the third step-20 signal) was
  deferred** — a separate `GET /assets/{id}/ocr` endpoint (a 2nd per-asset N+1) for
  a playbook-rated "minor" signal whose contract isn't in the pinned v3.0.2
  snapshot; full tradeoff write-up in [enrichment.md](/build/enrichment.md) step 20.
  This closes the enrichment playbook (17–20). Live-Immich re-verify of the
  rating/geocoding fields is deferred to the first real `sync`, same boundary as
  steps 4-5. **Next: only the optional testcontainers items 23/24 remain.**
- **Enrichment step 19 done; live-Claude caption spot-check deferred
  (2026-07-16):** `caption` now names people. A selected photo's assigned
  `person_ids` are resolved to `Person.name`s (`PhotoAnalyzer._resolve_person_names`,
  via the album-scoped `photo.album.people` cache) and passed as
  `PhotoContext.person_names` into the **one** shared analysis call, so a caption
  can say *"Alex and Sam at the harbour in Lisbon"*. Opt-in and additive: a
  face-free album — or a photo whose recognized people are all unnamed/unknown in
  Immich — produces no people line, leaving the prompt byte-for-byte as before
  (genericity guard). Tested via `TestModel`/SQLite only; the "spot-check ~20
  captions for accuracy and non-genericity" manual step (build-order step 12,
  enrichment 19) stays deferred — no `ANTHROPIC_API_KEY` here, same pattern as
  steps 9/10/12. **(Step 20 — rating/geocoded place — now done; see the
  2026-07-17 note above.)**
- **Step 25 done; live end-to-end run stays deferred (2026-07-16):**
  `flow.py` now runs the DAG from `pipeline-execution.md` instead of a
  straight chain — see the tracker row above for the shape. The manual
  `photobook-generator run --album honeymoon-2026` check (build-order.md step 14) is still
  blocked on a reachable Immich + `ANTHROPIC_API_KEY` + `darktable-cli` +
  Lulu creds, same standing deferral as every prior step; the new
  `test_flow_integration.py` is the automated substitute for the
  *concurrency* half of that check (real per-task sessions against a
  Postgres+pgvector container), not for the external services themselves.
- **Enrichment step 17b done; live reference-upload/detection deferred
  (2026-07-15):** `lead_faces` resolution is built and tested against a fake
  provider (upload → async-detection poll → `is_lead`), but the real
  `POST /assets` upload and Immich's *actual* async face-detection timing weren't
  exercised — no reachable Immich, same deferral as steps 4-5/9/17. On the first
  live run: confirm the reference upload dedups on re-run, that faces appear
  within the poll timeout (tune `LeadFaceResolver.poll_timeout_s` if Immich's job
  queue is slow), and that a lead unassigned in Immich surfaces the warning.
  **Next enrichment step: 18 (people/lead-aware `select`)** — the first consumer
  of `Person.is_lead`/`Photo.person_ids`. Idempotency corner (accepted): if a
  reference upload succeeds but the async-detection poll then times out, the run
  rolls back and loses the cached `lead_reference_asset_id`; content-dedup makes
  the re-upload return the same id, so it self-heals.
- **Enrichment step 17 done; live faces re-verification deferred (2026-07-15):**
  `sync` now fetches Immich faces/people into `Photo.face_count`/`person_ids` +
  the `Person` cache. Built and tested against recorded `/faces` fixtures and the
  `FakeImmich` double (schema-accurate to `AssetFaceResponseDto`), the same
  deferral pattern as steps 4-5/9 — no reachable live Immich in this sandbox, so
  the `?id=` contract and the async face-detection timing weren't exercised
  against a real instance. Re-verify the faces path on the first live `sync`,
  alongside the step-14/16 honeymoon/wedding re-runs. **Next enrichment step:
  17b (lead-face resolution).** The N+1 `get_faces` (one call per asset) is a
  known, accepted cost (documented in the playbook) — revisit only if a live run
  shows it dominating sync time.
- **Printer abstraction + HTML default output (2026-07-14, post-step-16,
  user-directed):** `fulfill` was generalized from a Lulu-only submitter to a
  pluggable [`Printer`](../../src/album/printers/base.py) — the output *medium* is
  now swappable. New `src/album/printers/` package: `base.py` (the `Printer`
  protocol + `iter_book_pages` shared page reader), `html.py`
  (`HtmlPrinter`, the new **default** — a self-contained page-by-page webpage
  rendered from the on-disk photos, no external service/credentials needed), and
  `lulu.py` (`LuluPrinter`, the old `LuluPrintJobAdapter` moved out of the stage;
  opt-in). `Album.lulu_order_id` was renamed to the medium-agnostic
  `Album.output_ref` (migration `b7f2c1a4e8d0`); `stages/fulfill.py` now depends
  only on the `Printer` protocol; `flow.py`'s `print_job_submitter` param became
  `printer`, defaulting to `HtmlPrinter()` (no LuluClient built or closed unless a
  `LuluPrinter` is injected). `album/lulu/client.py` (raw HTTP transport) is
  unchanged. Verified: `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check` green
  (165 tests). Self-reviewed via the `code-review` sub-agent. This means a default
  `photobook-generator run` now produces a real, viewable deliverable end-to-end (the webpage),
  no longer blocked on the Lulu PDF-hosting gap below.
- **Deferred live-GPS/live-Immich check (step 16):** `data/albums/smith-wedding
  /album.yml` proves the abstraction needs no code change for a structurally
  different (wedding, hour-scale, single-day) album — confirmed by a real
  `photobook-generator run --album smith-wedding` against real Postgres+Prefect, which
  bootstraps correctly and fails at the same `sync` boundary as every other
  run in this environment (no reachable Immich instance). Real GPS-tagged
  photos couldn't be attached for the same reason; the GPS spatial-clustering
  code path itself is already unit-tested (step 7). Re-run against a live
  Immich album with real, GPS-tagged wedding photos once one is reachable, to
  close this out for real alongside the step 14 honeymoon re-verification.
- **Full end-to-end run blocked on a reachable Immich instance (step 14):**
  `photobook-generator run` was verified against a real Postgres + Prefect server (real
  migrations, a real tracked flow run) but fails cleanly at `sync` with
  `ConnectionRefused` — no reachable Immich instance with real photos in
  this environment, a gap open since steps 4-5. Also still missing for a
  genuinely full run: `ANTHROPIC_API_KEY`, `darktable-cli` (only in the
  Docker app image, not this host), Lulu credentials, and the real
  LAION/open_clip quality scorer (step 6's deferral, still open — see
  `flow.py`'s `_UnavailableQualityModel`). Re-run `photobook-generator run --album
  honeymoon-2026` once all of these are available to close this out.
- **Real LAION/open_clip scorer still not wired (step 6, resurfaced at step
  14):** `flow.py`'s `_UnavailableQualityModel` is a raising placeholder —
  `quality()`'s per-photo failure isolation turns that into every photo
  landing with `blur_score`/`aesthetic_score`/`clip_embedding` left `NULL`.
  `select()` already has a tested fallback for that, so a run still
  completes, just with degraded curation. Wiring the real predictor
  (`open_clip` + `torch` + pretrained weights — a heavy, still-unadded
  dependency) is follow-up work, not blocking further build steps.
- **Deferred manual render verification (step 11) — CLOSED by step 22
  (2026-07-15):** `tests/test_render_integration.py` now runs the real
  `DarktableRenderer` inside the shipped app Docker image over a real source
  JPEG + a real `write_xmp_sidecar` sidecar and asserts the output is visibly
  corrected — the automated form of the build-11 manual check. The integration
  test also surfaced and validated a fix to `enhance`'s XMP sidecar format — see
  [enhance.md](/stages/enhance.md) History note. Still deferred: a real end-to-end run
  against a live Immich (the originals source) — same boundary as steps 4-5/14.
- **Deferred manual caption spot-check (step 12):** no `ANTHROPIC_API_KEY` in
  this environment, so `caption` is tested only via PydanticAI `TestModel`/
  `FunctionModel` — the build order's "spot-check ~20 captions for accuracy
  and non-genericity" manual step wasn't performed. Same deferral pattern as
  steps 4-5/9/15; do this once a key is available.
- **Parallel build (2026-07-14, per user):** steps 10 (`enhance`), 12
  (`caption`), 13 (`assemble`), and 15 (`fulfill`) were built concurrently on
  separate branches/worktrees rather than strictly in order (step 11
  `render` was also parallelized alongside 12, once 10 unblocked both — see
  its own entry once merged), since a dependency check showed each pair
  shares no files or logic. Each was self-reviewed (`code-review` skill)
  before merging; a coordinator (not the building agent) reconciled the two
  independently-added migrations (`assemble`'s real `Album.assembled_pdf_path`
  vs. `fulfill`'s placeholder of the same column)
  and updated this tracker/`log.md` centrally to avoid the three branches
  conflicting on shared docs. This is a deliberate, user-approved exception to
  the "steps are delivered strictly in order" rule above — not a new default.
- **No PDF-hosting/upload mechanism yet (step 15, still open — Lulu-only):**
  `Album.assembled_pdf_path` is a local disk path; Lulu's `source_url` needs a
  fetchable `http(s)://` URL. `LuluPrinter.print` raises `UnreachablePdfError`
  rather than submitting a path Lulu can't reach. Needs real hosting (static file
  serving, S3, etc.) wired in — only for the opt-in `LuluPrinter`, since the
  default `HtmlPrinter` needs no hosting.
- **Deferred live-Lulu check (step 15):** no Lulu sandbox credentials in this
  environment, so `LuluClient`/`fulfill` are tested only against recorded
  fixtures — the build order's "manually verify with a single-copy test order"
  step wasn't performed. Same deferral pattern as steps 4–5/9. `GET
  /print-jobs/{id}/`'s response schema was inferred from Lulu's OpenAPI spec,
  not directly confirmed — re-verify against a live sandbox call alongside the
  test order.
- **`enhance`/`caption` share one trigger (step 10):** `stages/enhance.py`'s
  `ensure_analysis()` is the idempotent "one combined vision call" trigger
  described in `architecture.md` — `caption` (step 12) should import and reuse
  it rather than reimplementing the same skip-if-already-populated check.
- **Deferred live-Claude check (step 10):** same reason and pattern as step 9
  below — no `ANTHROPIC_API_KEY` in this environment, so `enhance` is tested
  only via PydanticAI `TestModel`. Do this once a key is available.
- **Process change (from step 4 onward, per user):** deliver each step with the
  full workflow *except* the human-review gate — after `uv run poe check` is green
  and the sub-agent review is clean, squash and fast-forward-merge autonomously
  rather than waiting for user approval.
- **Toolchain landmine:** `ruff` 0.15.21's formatter, under `target-version =
  "py314"`, rewrites `except (A, B):` into invalid paren-less syntax (no fixed
  release yet). Avoid parenthesized multi-type `except` clauses — use separate
  clauses or `# fmt: skip`. See [log](/log.md) 2026-07-13.
- **Deferred live checks:** steps 4–5 fixtures/sync were built without a reachable
  Immich stack; re-verify against a live instance + the honeymoon album on the
  first real sync (see [Immich API notes](/reference/immich-api.md)).
- **Ryuk landmine (step 8) — RESOLVED 2026-07-15:** the Ryuk reaper now starts
  fine and the full suite is green with **no** `TESTCONTAINERS_RYUK_DISABLED`
  (plain `uv run poe check`). The earlier failure (`error while creating mount
  source path '/host_mnt/.../docker.sock'` — a Docker Desktop socket-mount
  restriction) went away with the Docker reinstall; `TESTCONTAINERS_RYUK_DISABLED`
  is no longer needed and the historical `uv run poe check` invocations in
  [log](/log.md) that set it are kept only as a record. Still relevant from the
  same step: `testcontainers`' `PostgresContainer` defaults to a `psycopg2`
  connection URL, but this project uses psycopg3 (`psycopg[binary]`), so pass
  `driver="psycopg"` explicitly (see `tests/test_similarity.py`).
- **Environment flakiness (step 9), resolved 2026-07-14:** during step 9, Docker
  Desktop became fully unresponsive in this sandbox (even `docker
  ps`/`docker system df` hung for minutes with no output — beyond the Ryuk
  landmine above), so `tests/test_similarity.py` (the pgvector testcontainer test
  from step 8) could not be re-verified for that step's final check; `uv run
  pytest --ignore=tests/test_similarity.py` plus lint/typecheck were used
  instead, and all 70 non-container tests passed. Nothing in step 9 touches
  `db/similarity.py` or Docker. Docker was reinstalled, and
  `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check` now passes clean
  (74/74 tests, including `test_similarity.py`) — closed, no further action.
- **Deferred live-Claude check (step 9):** `ANTHROPIC_API_KEY` isn't set in this
  environment's `.env`, so the agent (`agents/analysis.py`) is tested only via
  PydanticAI's `TestModel` — the build order's "wire in real Claude calls and
  sanity-check ~20 photos" manual step wasn't performed. Same deferral pattern
  as steps 4–5's live-Immich check. Do this once a key is available, ideally
  alongside step 10/12 (`enhance`/`caption`), the first real callers.

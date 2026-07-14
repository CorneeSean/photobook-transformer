---
type: Progress Tracker
title: Build progress
description: Living per-step status for the build order — updated by the implementing agent as each step moves through the workflow.
tags: [progress, status, tracking, build-order]
timestamp: 2026-07-12
---

# Build progress

Living status of the [build order](/build-order.md), maintained by the
implementing agent as part of the [implementation workflow](/implementation-workflow.md).
Update this file whenever a step changes state. Steps are delivered strictly in
order, one at a time.

**Status values:** `todo` → `in-progress` → `agent-review` → `awaiting-user` →
`done`. Use `blocked` (with a note) if a step cannot proceed.

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
| 14 | Wire the flow | — | todo | — | |
| 15 | `fulfill` stage | step-15-fulfill (merged, deleted) | done | 92a7d4c | Lulu Print API client (OAuth2 client-credentials); reads `Album.assembled_pdf_path`, writes `Album.lulu_order_id`; real Lulu sandbox order deferred (no credentials); no PDF-hosting/upload mechanism yet — see notes below |
| 16 | Prove genericity | — | todo | — | |

## Notes / blockers

- **Deferred manual render verification (step 11):** no `darktable-cli`
  binary on this host (it lives only in the Docker app image, see build step
  1) — the build order's "a rendered file exists, opens, and is visibly
  corrected versus its original" manual check wasn't performed. The
  orchestration around it (fetching originals, invoking the renderer,
  idempotency, failure isolation) is fully unit-tested with an injected
  `Renderer`/`OriginalSource` protocol instead. Same deferral pattern as
  steps 4-5/9/15/12; do this once the Docker app image is built and
  reachable.
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
- **No PDF-hosting/upload mechanism yet (step 15):** `Album.assembled_pdf_path`
  is a local disk path; Lulu's `source_url` needs a fetchable `http(s)://` URL.
  `LuluPrintJobAdapter.submit` raises `UnreachablePdfError` rather than
  submitting a path Lulu can't reach. Needs real hosting (static file serving,
  S3, etc.) wired in — likely alongside step 14 (flow) or whenever `fulfill` is
  first run for real.
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
  first real sync (see [Immich API notes](/immich-api.md)).
- **Toolchain landmine (step 8):** in this dev sandbox, `testcontainers`' Ryuk
  reaper container fails to start (`error while creating mount source path
  '/host_mnt/.../docker.sock'` — Docker Desktop socket-mount restriction under
  this sandbox). Run Postgres-testcontainer tests with
  `TESTCONTAINERS_RYUK_DISABLED=true` (e.g.
  `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check`). Also note
  `testcontainers`' `PostgresContainer` defaults to a `psycopg2` connection URL;
  this project uses psycopg3 (`psycopg[binary]`), so pass `driver="psycopg"`
  explicitly (see `tests/test_similarity.py`).
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

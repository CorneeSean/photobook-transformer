# Handover — start here

Photo Album Pipeline: a resumable batch pipeline that turns a raw photo dump into
a printed, curated photo book. **Steps 0–15 are done and merged to `main`; the
next job is step 16 (prove genericity — the last step).** `main` is clean and
`uv run poe check` is green (160 tests). Always take the lowest-numbered `todo` in
[`knowledge/progress.md`](./knowledge/progress.md) as the next step, and read its
**Notes / blockers** section first — it's long now (toolchain landmines, deferred
live-integration checks, the parallel-build note) and directly shapes how the
next step should be approached.

## How to start the conversation

Paste this to a fresh agent:

> Read `AGENTS.md`, then `knowledge/index.md` and
> `knowledge/implementation-workflow.md`. Check `knowledge/progress.md` for the
> next `todo` step (currently step 16) — read its **Notes / blockers** section
> first, in full. Implement **only that one step**, following the workflow
> (branch off `main`, TDD, then checks → sub-agent review → squash → fast-forward
> merge — the human-review gate is skipped per standing instruction, see the
> tracker's process-change note). Stop and show me your plan for the step before
> writing code.

## What to read first

1. [`AGENTS.md`](./AGENTS.md) — orientation and the non-negotiable rules.
2. [`knowledge/index.md`](./knowledge/index.md) — source of truth (OKF bundle).
3. [`knowledge/implementation-workflow.md`](./knowledge/implementation-workflow.md)
   — the per-step delivery loop (this is mandatory).
4. [`knowledge/progress.md`](./knowledge/progress.md) — current per-step status
   **and its "Notes / blockers" section — read this in full, not just skimmed**;
   it now documents several still-open deferred items step 16 will run into.
5. [`knowledge/build-order.md`](./knowledge/build-order.md) — the step to do, plus
   the concept docs it links (that's the step's context).

## Where things stand

Merged and `done` (see [`knowledge/progress.md`](./knowledge/progress.md) and
[`knowledge/log.md`](./knowledge/log.md) for full detail — this is a summary):

- **0 Scaffold** (`11d290f`) — `uv` project, ruff/basedpyright/pytest + poe tasks,
  `album` entry point, package skeleton, pre-commit (`no-commit-to-branch`).
- **1 Containers** (`240fbe0`) — `Dockerfile` + `docker-compose.yml` (our app +
  our own `pgvector` Postgres + Prefect), Immich vendored in
  `docker-compose.immich.yml` via Compose `include:`.
- **2 Data model + migrations** (`68ed96f`) — `src/album/db/models.py`
  (`Album`/`Segment`/`Photo`) + first alembic migration.
- **3 Album definition loading** (`84260ef`) — `config.py` + `definition.py`
  (`album.yml` → `Album`/`Segment` rows, UTC normalization).
- **4+5 Immich client + `sync`** (`8172dfa`) — typed `httpx` wrapper +
  idempotent Immich→DB boundary. Immich exposes no CLIP embeddings (Open Q1).
- **6 `quality`** (`a6c4b9e`) — real OpenCV blur; real LAION/open_clip aesthetic
  scorer + CLIP embedder **still deferred** (heavy, unadded dependency) — see
  the tracker's notes, resurfaced at step 14.
- **7 `cluster`** (`2a39df5`) — segment assignment + temporal/spatial(DBSCAN)
  scene detection, with no-segments/no-GPS fallbacks.
- **8 `select`** (`4fec4a1`) — diversity-aware MMR + `db/similarity.py`
  (pgvector behind a substitutable interface).
- **9 Agent** (`38a4057`) — `agents/analysis.py`, the single combined vision
  call (bounded tone deltas + caption in one request). Live-Claude check
  deferred (no `ANTHROPIC_API_KEY`).
- **10 `enhance`** (`e7fccfe`) — cross-camera exposure baseline
  (`skimage.exposure.match_histograms`) + darktable-native XMP sidecars;
  `ensure_analysis()` is the shared idempotent trigger `caption` also uses.
- **11 `render`** (`168940f`) — `darktable-cli` batch invocation; added
  `ImmichClient.get_original()`. Manual "visibly corrected JPEG" check
  deferred — no `darktable-cli` on a bare host, only in the Docker app image.
- **12 `caption`** (`69f82b3`) — thin wrapper reusing `enhance`'s
  `ensure_analysis()`; order-independent with `enhance`.
- **13 `assemble`** (`9ec5ca7`) — resolves Open Q4 (Lulu 8.5×8.5in trim,
  0.125in bleed, spine formula); Jinja2 + WeasyPrint → PDF; adds
  `Album.assembled_pdf_path`.
- **14 Wire the flow** (`ada27d6`) — `flow.py` (the only Prefect import) +
  `cli.py`'s `run` subcommand. **Verified against a real Postgres + Prefect
  server** (real migrations, a real tracked flow run) — fails cleanly at
  `sync` with no reachable Immich instance, the one boundary this sandbox
  can't cross. See the tracker's notes for the full list of what's still
  needed for a genuinely live run.
- **15 `fulfill`** (`92a7d4c`) — Lulu Print API client (OAuth2
  client-credentials). **Still open:** no PDF-hosting/upload mechanism, so
  a real submission raises `UnreachablePdfError` rather than send Lulu an
  unfetchable local path.

## Next step

**Step 16 — prove genericity** (the last build step). Read its entry in
[build-order.md](./knowledge/build-order.md): run a *structurally different*
album end-to-end (a wedding — hour-scale segments, single day, GPS present —
or a segment-less dump) and confirm it needs **only** `album.yml` + an Immich
album, no code change. This is blocked on the same infrastructure gap step 14
hit: no reachable Immich instance with real photos in this sandbox. What *can*
be done without live infra (a static/structural genericity audit of `src/`,
plus whatever the next agent judges reasonable) vs. what must stay deferred is
a judgment call for whoever picks this up — read the tracker's notes first.

## Parallelization

Steps that don't share files or logic can be built concurrently on separate
git worktrees/branches (a deliberate, user-approved exception to "strictly in
order" — see the tracker's 2026-07-14 parallel-build note for exactly how
that was done for steps 10+13+15 and 11+12: dependency-check first, self-review
each branch, one coordinator merges serially and reconciles any shared-file
conflicts, e.g. two branches independently adding the same DB column). Step 16
is the only step left, so this doesn't apply right now — but check for this
opportunity on any future multi-step batch of work.

## Golden rules

- **Never work on or commit to `main`.** One step per branch.
- **Order is fixed:** checks green → sub-agent (automated) review → commit. The
  human-review gate is skipped per the user's standing instruction; merge
  autonomously once checks + sub-agent review are clean. Re-run checks after every
  change round — **including immediately before the commit** (a `ruff`/py314 bug
  can silently reintroduce invalid syntax; see log 2026-07-13).
- Update `knowledge/progress.md` and `knowledge/log.md` **on the step branch,
  before the fast-forward merge** — `main` is commit-protected, so a post-merge
  docs commit can't land without a second branch.
- **`TESTCONTAINERS_RYUK_DISABLED=true`** is needed for `uv run poe check` in
  this kind of sandbox (Ryuk reaper landmine, see tracker notes) — Docker
  itself works fine here (confirmed step 14: real Postgres + Prefect server
  brought up and torn down cleanly via `docker compose`).

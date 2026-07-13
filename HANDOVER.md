# Handover — start here

Photo Album Pipeline: a resumable batch pipeline that turns a raw photo dump into
a printed, curated photo book. **Steps 0–6 are done and merged to `main`; the
next job is step 7 (the `cluster` stage).** `main` is clean and `uv run poe check`
is green. Always take the lowest-numbered `todo` in
[`knowledge/progress.md`](./knowledge/progress.md) as the next step, and read its
**Notes / blockers** section first (it records the active process change and a
`ruff`/py314 toolchain landmine).

## How to start the conversation

Paste this to a fresh agent:

> Read `AGENTS.md`, then `knowledge/index.md` and
> `knowledge/implementation-workflow.md`. Check `knowledge/progress.md` for the
> next `todo` step and implement **only that one step**, following the workflow
> (branch off `main`, TDD, then checks → sub-agent review → user review → squash
> → fast-forward merge). Stop and show me your plan for the step before writing
> code.

## What to read first

1. [`AGENTS.md`](./AGENTS.md) — orientation and the non-negotiable rules.
2. [`knowledge/index.md`](./knowledge/index.md) — source of truth (OKF bundle).
3. [`knowledge/implementation-workflow.md`](./knowledge/implementation-workflow.md)
   — the per-step delivery loop (this is mandatory).
4. [`knowledge/progress.md`](./knowledge/progress.md) — current per-step status.
5. [`knowledge/build-order.md`](./knowledge/build-order.md) — the step to do, plus
   the concept docs it links (that's the step's context).

## Where things stand

Merged and `done` (see [`knowledge/progress.md`](./knowledge/progress.md) and
[`knowledge/log.md`](./knowledge/log.md) for detail):

- **0 Scaffold** (`11d290f`) — `uv` project, ruff/basedpyright/pytest + poe tasks,
  `album` entry point, package skeleton, pre-commit (`no-commit-to-branch`).
- **1 Containers** (`240fbe0`) — `Dockerfile` + `docker-compose.yml` (our app +
  our own `pgvector` Postgres + Prefect), Immich vendored in
  `docker-compose.immich.yml` via Compose `include:`.
- **2 Data model + migrations** (`68ed96f`) — `src/album/db/models.py`
  (`Album`/`Segment`/`Photo`) + first alembic migration
  (`CREATE EXTENSION IF NOT EXISTS vector` before the `Vector(768)` column).
- **3 Album definition loading** (`84260ef`) — `src/album/config.py`
  (pydantic-settings, the only env reader) + `src/album/definition.py`
  (`album.yml` → `Album`/`Segment` rows, all times normalized to UTC).
- **4 + 5 Immich client + `sync`** (`8172dfa`, delivered together) —
  `src/album/immich/client.py` (typed `httpx` wrapper) + `src/album/stages/sync.py`
  (idempotent Immich→DB boundary, UTC normalization) + `db/session.py`. Resolved
  open question #1 (Immich exposes **no** CLIP embeddings; see
  [Immich API notes](./knowledge/immich-api.md)). Live verification deferred.
- **6 `quality`** (`a6c4b9e`) — `src/album/stages/quality.py`: real OpenCV blur +
  injected `AestheticScorer`/`Embedder` protocols (real LAION/open_clip impls
  deferred as heavy; wire at step 14).

## Next step

Take the lowest-numbered `todo` in [`knowledge/progress.md`](./knowledge/progress.md)
— currently **step 6 (`quality`)** — and read that step's entry in
[build-order.md](./knowledge/build-order.md) plus the concept docs it links.
**Read the progress tracker's "Notes / blockers" first**: it records the active
process change and the `ruff`/py314 toolchain landmine.

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

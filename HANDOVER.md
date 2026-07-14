# Handover — start here

Photo Album Pipeline: a resumable batch pipeline that turns a raw photo dump into
a printed, curated photo book. **All 16 build steps are done and merged to
`main` — the build order is complete.** `main` is clean and `uv run poe check`
is green (161 tests, run with `TESTCONTAINERS_RYUK_DISABLED=true` in this kind
of sandbox — see Golden rules). There is no next numbered step; what's left is
either **closing a deferred live-integration check** (see
[`knowledge/progress.md`](./knowledge/progress.md)'s Notes / blockers section —
read it in full, it's long) or new, user-directed work.

## How to start the conversation

There's no fixed next build step anymore, so there's no one canonical prompt.
For closing a deferred item or new work, a fresh agent should still:

> Read `AGENTS.md`, then `knowledge/index.md` and
> `knowledge/implementation-workflow.md`. Read `knowledge/progress.md`'s
> **Notes / blockers** section in full — it lists everything still deferred
> (live Immich/Claude/Lulu/darktable-cli checks) and any new work the user
> describes. Follow the same workflow used for every build step (branch off
> `main`, TDD where applicable, then checks → sub-agent review → squash →
> fast-forward merge — the human-review gate is skipped per standing
> instruction, see the tracker's process-change note). Stop and show a plan
> before writing code.

## What to read first

1. [`AGENTS.md`](./AGENTS.md) — orientation and the non-negotiable rules.
2. [`knowledge/index.md`](./knowledge/index.md) — source of truth (OKF bundle).
3. [`knowledge/implementation-workflow.md`](./knowledge/implementation-workflow.md)
   — the per-step delivery loop (this is mandatory).
4. [`knowledge/progress.md`](./knowledge/progress.md) — current per-step status
   **and its "Notes / blockers" section — read this in full, not just skimmed**;
   it documents every still-open deferred item.
5. [`knowledge/build-order.md`](./knowledge/build-order.md) — the full,
   now-complete step list, for context on how each stage was built.

## Where things stand

All merged and `done` (see [`knowledge/progress.md`](./knowledge/progress.md)
and [`knowledge/log.md`](./knowledge/log.md) for full detail — this is a
summary):

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
- **16 Prove genericity** (`d7e903d`) — added
  `data/albums/smith-wedding/album.yml`, a real, structurally different
  (wedding-shaped: hour-scale segments, single day) album, plus
  `tests/test_genericity.py`. **Zero `src/` files changed** — confirmed the
  abstraction. Live run verified against real Postgres+Prefect: bootstraps
  correctly, fails at the same no-reachable-Immich `sync` boundary as step
  14. The build order is now complete.

## What's left — closing deferred items, not new build steps

Every remaining gap is the same shape: this sandbox has no reachable live
Immich instance, `ANTHROPIC_API_KEY`, `darktable-cli` binary (only in the
Docker app image), or Lulu sandbox credentials. Nothing is blocked on missing
code — see [`knowledge/progress.md`](./knowledge/progress.md)'s Notes /
blockers section for the full, current list, including:

- A genuinely full `album run --album <slug>` (real sync → real quality
  scoring → ... → real Lulu order) once all four are available.
- The real LAION/open_clip aesthetic scorer + CLIP embedder (step 6's
  deferral) — `_UnavailableQualityModel` in `flow.py` stands in for it.
- A PDF-hosting/upload mechanism so `fulfill` can give Lulu a fetchable
  `http(s)://` URL instead of a local path (step 15's gap).
- Manual spot-checks that need real data: darktable render quality, caption
  accuracy/non-genericity, and a live Lulu single-copy test order.

## Parallelization

Steps that don't share files or logic can be built concurrently on separate
git worktrees/branches (a deliberate, user-approved exception to "strictly in
order" — see the tracker's 2026-07-14 parallel-build note for exactly how
that was done for steps 10+13+15 and 11+12: dependency-check first, self-review
each branch, one coordinator merges serially and reconciles any shared-file
conflicts, e.g. two branches independently adding the same DB column). Applies
to future deferred-item or new-feature work too, if it splits similarly.

## Golden rules

- **Never work on or commit to `main`.** One unit of work per branch, even for
  docs-only changes — the `no-commit-to-branch` hook blocks *any* direct
  commit to `main`.
- **Order is fixed:** checks green → sub-agent (automated) review → commit. The
  human-review gate is skipped per the user's standing instruction; merge
  autonomously once checks + sub-agent review are clean. Re-run checks after every
  change round — **including immediately before the commit** (a `ruff`/py314 bug
  can silently reintroduce invalid syntax; see log 2026-07-13).
- Update `knowledge/progress.md` and `knowledge/log.md` **on the working
  branch, before the fast-forward merge** — `main` is commit-protected, so a
  post-merge docs commit can't land without a second branch.
- **`TESTCONTAINERS_RYUK_DISABLED=true`** is needed for `uv run poe check` in
  this kind of sandbox (Ryuk reaper landmine, see tracker notes) — Docker
  itself works fine here (confirmed steps 14 and 16: real Postgres + Prefect
  server brought up and torn down cleanly via `docker compose`).

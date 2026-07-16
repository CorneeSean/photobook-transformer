# AGENTS.md

Orientation for coding agents working in this repo. Read this first, then treat
the **[`knowledge/`](./knowledge/index.md) OKF bundle as the source of truth**
for the domain model, architecture decisions (and their rationale), the full
pipeline stage list, the data model, and the step-by-step build order. This file
is the short operational summary; the bundle is the detail. If the two ever
conflict, the bundle wins — and fix this file.

The bundle is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
directory: start at [`knowledge/index.md`](./knowledge/index.md), which links to
one document per concept (each with `type` frontmatter), plus a `stages/`
subdirectory with one document per pipeline stage.

## What this project is

A batch pipeline that turns a raw dump of photos into a curated, enhanced,
captioned, print-ready photo book, submitted to a print API — with minimal
manual work. It is **event-type agnostic**: trips, weddings, a baby's first
year, a festival are all just *albums*. The abstraction is `Album` + optional
`Segment` (a named, time-bounded, optionally-placed period). See
[`knowledge/foundations/domain-model.md`](./knowledge/foundations/domain-model.md) before touching
anything that reasons about structure.

## Current state

All 16 steps of the numbered [**Build order**](./knowledge/build/build-order.md) are
done and merged to `main` — every stage (`sync` through `fulfill`) is
implemented, wired into one Prefect flow, and step 16 confirmed the
`Album`/`Segment` abstraction needs no code change for a structurally
different album. Since then (post-step-16, user-directed) `fulfill` was
generalized to a pluggable `Printer` (`src/album/printers/`), with a browsable
HTML book (`HtmlPrinter`) as the default output and Lulu opt-in — see
[`knowledge/build/progress.md`](./knowledge/build/progress.md)'s Notes / blockers section.
See [`HANDOVER.md`](./HANDOVER.md) for a full summary and
[`knowledge/build/progress.md`](./knowledge/build/progress.md)'s Notes / blockers section
for what's still deferred (live-Immich/Claude/Lulu/darktable-cli checks — this
sandbox has no reachable instances of any of them, so those are the live
follow-ups, not new build steps). There is no more build-order work queued;
further changes are either closing a deferred item or new, user-directed work
— both still follow the workflow described below (branch, checks, review,
squash-merge).

## How a step is implemented (read before writing any code)

Deliver **one build step at a time**, following
[`knowledge/process/implementation-workflow.md`](./knowledge/process/implementation-workflow.md),
and keep [`knowledge/build/progress.md`](./knowledge/build/progress.md) current. The order is
fixed — **checks → sub-agent review → user review → commit** — and any change
re-runs what precedes it. In short:

1. Take the next `todo` step from the [progress tracker](./knowledge/build/progress.md).
2. **Branch off `main`** (`step-NN-<slug>`) — **never work on or commit to `main`**
   (a `no-commit-to-branch` pre-commit hook enforces this).
3. Load the step's context (its [build-order](./knowledge/build/build-order.md) entry +
   every doc it links) and implement **test-first**. Subcommits are fine.
4. **Get `uv run poe check` green first** (ruff + basedpyright + pytest), or do
   the step's manual verification. No review starts until checks are green.
5. **Then invoke a review sub-agent** on the diff; address comments, **re-run
   `poe check` to green**, re-review — until it has no comments. **Cap this at a
   maximum of 3 essential, broad-scope review sub-agents** (correctness /
   cleanup / tests-conventions) — don't fan out into many narrow angles; use
   fewer for a small diff. See implementation-workflow.md step 6.
6. **Then ask the user to review;** on requested changes, fix, **re-run `poe
   check`**, **re-run the sub-agent review**, then return to the user — until
   approved.
7. **Squash** subcommits into one `<stage>: <what changed>` commit, **then update
   the tracker + `log.md`** in a commit on top of that (still on the step branch
   — `main` is commit-protected, so this can't happen after the merge), **then
   fast-forward merge** to `main`, **push `main` to `origin`**, and delete the
   branch.

Commit to `main` **only** when checks are green, the sub-agent is satisfied, and
the user has approved — in that order.

## Workflow commands

Once scaffolded, workflow actions are `poethepoet` tasks run via `uv run poe`:

| Command | What it does |
|---|---|
| `uv run poe check` | ruff + `ruff format --check` + basedpyright + pytest — **the commit gate** |
| `uv run poe lint` / `poe format` | `ruff check .` / `ruff format .` |
| `uv run poe typecheck` | `basedpyright` |
| `uv run poe test` | `pytest` — the whole suite (unit + integration) |
| `uv run poe test-unit` | `pytest -m "not integration"` — pure-logic tests only (fast) |
| `uv run poe test-integration` | `pytest -m integration` — containers + real HTTP-client boundary |
| `uv run poe up` / `poe down` | `docker compose up` / `down` (app + Postgres + Immich + Prefect) |
| `uv run poe migrate` | `alembic upgrade head` |
| `uv run poe run -- --album <slug>` | run the pipeline flow for one album |

The pipeline is also a console entry point: `uv run album run --album <slug>`.

## Non-negotiable rules

These are the guardrails that keep the design honest. Enforce them in tests, not
just prose.

1. **Genericity.** Nothing in `src/` may reference trips, itineraries, days, or
   any occasion-specific concept. If a stage needs to know an album is a "trip,"
   the abstraction has leaked — fix the abstraction, don't special-case.
2. **Segments are optional.** Every stage that reads segments must have a
   *tested* fallback for an album with none (and, where relevant, no GPS).
3. **`album_id` first.** Every stage function takes `album_id` as its first
   argument; no stage assumes it's the only album in the DB.
4. **No Prefect in `flow/stages/`.** `flow/stages/*.py` are plain, unit-testable
   functions. Only `flow/_flow.py` imports Prefect and wraps them as `@task`s.
5. **AI estimates parameters, never generates pixels.** Enhancement produces
   bounded numeric deltas (Pydantic `Field(ge=, le=)`); classical code applies
   them. No invented detail.
6. **`config.py` is the only place that reads env vars.**
7. **Idempotent stages.** Re-running a stage on a processed album must be safe
   and produce the same result. Nullability in the schema is meaningful (`NULL`
   = "that stage hasn't run for this photo yet") — that's what makes it
   resumable.
8. **Migrations, always.** Use alembic; never `Base.metadata.create_all()`.

## How work is done

- **Test-driven.** Write the failing pytest first (against the contract), watch
  it fail for the right reason, then implement the minimum to pass, then
  refactor. Pure-logic stages are the highest-value tests — write them
  thoroughly. See the per-stage testing table in
  [`knowledge/process/working-agreement.md`](./knowledge/process/working-agreement.md) for what to
  use (in-memory SQLite, `testcontainers` for `pgvector`, `respx`/`vcrpy` for the
  Immich client, PydanticAI `TestModel` for agents).
- **Static typing is a gate.** The code is heavily typed (SQLAlchemy 2.0
  `Mapped[...]`, Pydantic v2). `basedpyright` must be clean before commit.
  (Astral's `ty` is the planned future checker — don't assume a specific one.)
- **One squashed commit per build step, on a branch, merged to `main` and pushed
  to `origin`** — never a direct commit to `main`, never one big dump. A step is done only when
  `uv run poe check` is green (or the manual verification is performed), **then**
  the review sub-agent is satisfied, **then** the user has approved — in that
  order, re-running checks after each round. Put the verification in the commit
  body. Commit format: `<stage>: <what changed>`. See
  [`implementation-workflow.md`](./knowledge/process/implementation-workflow.md).
- **If a step can't be completed cleanly, do not commit and move on.** Stop and
  surface the problem.

## Layout (target)

See [`knowledge/reference/folder-structure.md`](./knowledge/reference/folder-structure.md) for the
full tree and rules. In brief:

```
src/album/
  config.py      cli.py      definition.py
  flow/          _flow.py (the one Prefect @flow) tone.py photo_analyzer.py
  flow/stages/   sync quality cluster select enhance render caption assemble fulfill
  db/            models.py similarity.py session.py
  immich/        client.py
  lulu/          client.py     (thin Lulu Print API HTTP transport)
  printers/      base.py html.py lulu.py  (pluggable output media; HtmlPrinter is default)
  agents/        analysis.py   (ONE agent: tone deltas + caption, swap for TestModel)
templates/       Jinja2 page layouts (+ book/ for the HTML viewer)
data/albums/     <slug>/album.yml (the only thing needed to define an album)
tests/           conftest.py + per-stage tests + fixtures/
```

Adding a new album must require **only** an `album.yml` plus an Immich album —
no code change. That's the real test of the abstraction (build step 16).

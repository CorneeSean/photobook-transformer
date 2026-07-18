# AGENTS.md

Orientation for coding agents working in this repo. Read this first, then treat
the **[`knowledge/`](./knowledge/index.md) OKF bundle as the source of truth**
for the domain model, architecture (and rationale), the pipeline stages, the data
model, and the build order. This file is the short operational summary; the bundle
is the detail. **If the two ever conflict, the bundle wins — and fix this file.**

The bundle is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
directory: start at [`knowledge/index.md`](./knowledge/index.md).

## What this project is

A batch pipeline that turns a raw dump of photos into a curated, enhanced,
captioned, print-ready photo book — with minimal manual work. It is
**event-type agnostic**: trips, weddings, a baby's first year, a festival are all
just *albums* (`Album` + optional `Segment`). Read
[`domain-model.md`](./knowledge/foundations/domain-model.md) before touching
anything that reasons about structure.

## Current state

The build order and every follow-on playbook are done and merged to `main`:
build order (0–16), Immich enrichment (17–20), TestContainers integration
(21–25), single-command ingestion (26–28), and offline analysis (29). Only the
**optional step 23** (DB-backed flow smoke test) is outstanding. The pipeline is
**fully offline by default** (classical tone + template caption, no API key);
Claude is an opt-in caption upgrade (`--captioner claude`). See
[`progress.md`](./knowledge/build/progress.md) — its **Notes / blockers** section
is the current list of everything still deferred (all "no reachable live
Immich / `ANTHROPIC_API_KEY` / `darktable-cli` / Lulu creds in this sandbox"
follow-ups, not new code).

There is no queued build work. Further changes are either closing a deferred item
or new, user-directed work — both still follow the workflow below.

## Starting a session

A fresh agent should read `AGENTS.md`, then
[`knowledge/index.md`](./knowledge/index.md) and
[`implementation-workflow.md`](./knowledge/process/implementation-workflow.md),
then [`progress.md`](./knowledge/build/progress.md)'s **Notes / blockers** in
full. Follow the same per-step workflow (branch off `main`, TDD where applicable,
then checks → sub-agent review → squash → fast-forward merge → push) for any new
or deferred-item work. **Stop and show a plan before writing code.**

## How a step is implemented

The full loop is [`implementation-workflow.md`](./knowledge/process/implementation-workflow.md)
(mandatory); the quality gate is [`working-agreement.md`](./knowledge/process/working-agreement.md).
The order is fixed — **checks → sub-agent review → user review → commit** — and
any change re-runs what precedes it. In short:

1. Take the next `todo` step from the [tracker](./knowledge/build/progress.md).
2. **Branch off `main`** (`step-NN-<slug>`) — **never commit to `main`** (a
   `no-commit-to-branch` hook enforces it).
3. Load the step's context and implement **test-first**.
4. Get **`uv run poe check` green** (or the step's manual verification) — no review
   starts until it is.
5. **Sub-agent review** the diff — **up to 4 broad-scope agents** (2 adversarial
   correctness, 1 style/reuse, 1 architecture; scale down for small diffs). Address
   comments, re-run `poe check`, re-review until clean.
6. **User review** (skipped per standing instruction — merge autonomously once
   checks + sub-agent review are clean; see the tracker's process-change note).
7. **Squash** into one `<stage>: <what changed>` commit, **update the tracker +
   `log.md`** on the branch, **fast-forward merge** to `main`, **push to
   `origin`**, delete the branch.

## Workflow commands

The canonical `poe` task table lives in
[`working-agreement.md`](./knowledge/process/working-agreement.md#local-workflow-commands).
The commit gate is **`uv run poe check`** (ruff + `ruff format --check` +
basedpyright + xenon complexity + pytest). The pipeline is a console entry point:
`uv run photobook-generator generate <dir>` (one-command path) or
`run --album <slug>` (hand-authored `album.yml`).

## Non-negotiable rules

Guardrails that keep the design honest — enforce them in tests, not just prose.
Detail and rationale live in
[`folder-structure.md`](./knowledge/reference/folder-structure.md) and
[`quality-practices.md`](./knowledge/process/quality-practices.md).

1. **Genericity.** Nothing in `src/` may reference trips, itineraries, days, or any
   occasion-specific concept. If a stage needs to know an album is a "trip," the
   abstraction has leaked — fix the abstraction, don't special-case.
2. **Segments are optional.** Every stage that reads segments must have a *tested*
   fallback for an album with none (and, where relevant, no GPS).
3. **`album_id` first.** Every stage **class** is constructed with `album_id` (and
   a `session_factory`) as its first per-run inputs; no stage assumes it's the only
   album in the DB.
4. **A stage *is* its own Prefect task.** Each stage class subclasses
   `flow.stages.base.Stage` and is turned into a named Prefect task by the
   `TaskUnit` base — there are **no module-level `@task` functions and no
   `flow/_tasks.py`**. The stage class stays Prefect-free at module scope
   (unit-tested via `run`/`_process`); `flow/_flow.py` is the aggregator that
   composes the DAG. `prefect` is imported only by `flow/stages/base.py` and
   `flow/_flow.py` — never in `db/`, `immich/`, `lulu/`, `printers/`, `analysis/`,
   `agents/`, `config.py`, `definition.py`.
5. **Enhancement estimates bounded numeric deltas, never generates pixels.** Tone
   deltas are Pydantic `Field(ge=, le=)`; classical code applies them via XMP. Tone
   is **classical/offline by default** (`ClassicalToneEstimator`); no invented
   detail.
6. **`config.py` is the only place that reads env vars.**
7. **Idempotent stages.** Re-running a stage on a processed album must be safe and
   produce the same result. `NULL` = "that stage hasn't run for this photo yet" —
   that's what makes it resumable.
8. **Migrations, always.** Use alembic; never `Base.metadata.create_all()`.

## How work is done

Test-driven (failing pytest first — see the per-stage testing table in
[`working-agreement.md`](./knowledge/process/working-agreement.md)), heavily typed
(`basedpyright` clean is a gate), one squashed commit per step on a branch merged
to `main` and pushed. If a step can't be completed cleanly, **stop and surface the
problem** — don't commit and move on. Structure and testing conventions:
[`quality-practices.md`](./knowledge/process/quality-practices.md). Target layout:
[`folder-structure.md`](./knowledge/reference/folder-structure.md).

Adding a new album must require **only** an `album.yml` plus an Immich album — no
code change. That's the real test of the abstraction (build step 16).

# Handover — start here

Photo Album Pipeline: a resumable batch pipeline that turns a raw photo dump into
a printed, curated photo book. **Right now the repo is docs only — no code yet.**
`main` holds the planning docs; the next job is to write the first code.

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

## Next step

**Step 0 — Scaffold** (status `todo`). On a `step-00-scaffold` branch: `uv init`
(Python 3.14); configure ruff, basedpyright, pytest, and `poethepoet` tasks in
`pyproject.toml`; add the `album` console entry point; create the folder
structure; add `.pre-commit-config.yaml` (`no-commit-to-branch` + ruff +
basedpyright) and `pre-commit install`; write `README.md`. Done when `uv run poe
check` runs green and a direct commit to `main` is rejected by the hook — then
squash, get sub-agent + user review, and fast-forward merge.

## Golden rules

- **Never work on or commit to `main`.** One step per branch.
- **Order is fixed:** checks green → sub-agent review → user review → commit;
  re-run checks after every change round.
- Update `knowledge/progress.md` and `knowledge/log.md` as steps move.

---
type: Playbook
title: Implementation workflow (per-step handoff)
description: How an agent implements one build step at a time — branch, TDD, sub-agent review, user review, checks, squash, merge.
tags: [workflow, process, review, git, branching, handoff]
timestamp: 2026-07-12
---

# Implementation workflow (per-step handoff)

This is the loop an implementing agent follows to deliver the project **one
[build step](/build-order.md) at a time**. It is mandatory. The
[working agreement](/working-agreement.md) defines the *quality* gate (TDD,
typing, `uv run poe check`); this document defines the *delivery* gate (branch,
review chain, squash, merge) around it.

## Hard rules

- **One step at a time.** Do not start step N+1 until step N is merged and marked
  `done` in the [progress tracker](/progress.md).
- **Never work on or commit to `main`.** Every step is done on its own branch and
  merged. `main` only ever advances by merging a reviewed, approved step branch.
- **Commit to `main` (via merge) only when ALL of these hold:** the review
  sub-agent has no remaining comments, **the user has approved**, and
  `uv run poe check` is green (plus any manual verification the step requires).
- **Subcommits are allowed on the step branch, but squash before merge** — the
  step lands on `main` as a single, clean commit.
- **Reviews run on green checks, and every change re-runs them.** The order is
  fixed: **checks → sub-agent review → user review → commit**. `uv run poe check`
  must be green *immediately before each review*; any change made in response to a
  review round re-runs the checks to green (and re-runs the sub-agent review
  before returning to the user).

## The loop for one step

1. **Select the step.** Take the lowest-numbered step whose status is `todo` in
   the [progress tracker](/progress.md). Steps are strictly ordered.
2. **Branch off `main`.** `git switch main && git pull` (if a remote exists), then
   `git switch -c step-NN-<slug>` (e.g. `step-02-data-model`). Update the tracker:
   set the step `in-progress` and record the branch name.
3. **Load the context** (see "Context to load" below) so the work respects the
   established design and the [non-negotiable rules](/folder-structure.md).
4. **Implement test-first.** Follow the [TDD loop](/working-agreement.md): failing
   test → minimum implementation → refactor. Make as many **subcommits** as help;
   they will be squashed.
5. **Self-check — the gate for every review.** Run `uv run poe check` (ruff +
   basedpyright + pytest) until green. For a step with no automated test, perform
   the manual verification the [build step](/build-order.md) describes and write
   down the result. **No review starts until checks are green.**
6. **Sub-agent review (only on green checks).** Invoke a **review sub-agent** on
   the step's diff (`main..HEAD`) — e.g. via the repo's code-review tooling —
   giving it the step's context. If it has comments: address them, **re-run
   `uv run poe check` until green**, then re-review. **Repeat until the reviewer
   returns no further comments.** Record `agent-review: passed` in the tracker.
7. **User review (only on green checks + a satisfied sub-agent).** Present the
   step to the **user** (summary of what changed, how it was verified, the diff).
   If the user requests changes: make them, **re-run `uv run poe check` until
   green**, **re-run the sub-agent review until it has no comments**, then return
   to the user. Only a clear user approval advances the step.
8. **Finalize.** With user approval in hand and `uv run poe check` green (and
   manual verification done, if any): **squash** the branch's subcommits into one
   commit using the [commit format](/working-agreement.md)
   (`<stage>: <what changed>`, verification in the body).
9. **Merge.** Merge the step branch into `main` via **fast-forward** (linear
   history, no merge commit — which also keeps the `no-commit-to-branch`
   pre-commit hook satisfied), then delete the branch. Never force-push `main`.
10. **Record.** Update the [progress tracker](/progress.md): status `done`, the
    merge commit hash, and the date. Add a dated entry to [`log.md`](/log.md).
11. **Next.** Return to step 1 for the following build step.

If a step cannot be completed cleanly, **stop and surface the problem** (mark it
`blocked` in the tracker with a note) rather than committing partial work or
moving on.

## Context to load before starting a step

For step N, read, in this order:

1. This workflow document (the loop and the hard rules).
2. The step's entry in the [build order](/build-order.md) — and **every concept
   document it links** (the relevant stage doc, [data model](/data-model.md),
   [domain model](/domain-model.md), etc.). Those links are the step's context.
3. The [non-negotiable structure rules](/folder-structure.md) and the
   [working agreement](/working-agreement.md) (TDD table, typing gate, commit
   gate).
4. The [progress tracker](/progress.md) — what is already `done`, and any notes
   on `blocked` items.
5. Any [open questions](/open-questions.md) the step is tasked with resolving.

## Definition of done for a step

A step is `done` only when **all** are true:

- [ ] Work happened on a `step-NN-<slug>` branch, not `main`.
- [ ] `uv run poe check` is green (ruff, basedpyright, pytest — no regressions).
- [ ] Manual verification performed and recorded, if the step has no automated test.
- [ ] The review sub-agent has no remaining comments.
- [ ] The user has explicitly approved.
- [ ] Subcommits squashed into one `<stage>: <what changed>` commit with the
      verification in the body.
- [ ] Branch merged into `main`, branch deleted.
- [ ] [Progress tracker](/progress.md) and [`log.md`](/log.md) updated.

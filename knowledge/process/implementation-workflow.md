---
type: Playbook
title: Implementation workflow (per-step handoff)
description: How an agent implements one build step at a time — branch, TDD, sub-agent review (up to 4 broad-scope agents — 2 adversarial, style, architecture), user review, checks, squash, merge, push.
tags: [workflow, process, review, git, branching, handoff]
timestamp: 2026-07-12
---

# Implementation workflow (per-step handoff)

This is the loop an implementing agent follows to deliver the project **one
[build step](/build/build-order.md) at a time**. It is mandatory. The
[working agreement](/process/working-agreement.md) defines the *quality* gate (TDD,
typing, `uv run poe check`); this document defines the *delivery* gate (branch,
review chain, squash, merge, push) around it.

## Hard rules

- **One step at a time.** Don't start step N+1 until step N is merged and `done`.
- **Never work on or commit to `main`.** Every step is a branch merged in; `main`
  only advances by merging a reviewed, approved step branch (`no-commit-to-branch`
  hook enforces it).
- **Merge only when ALL hold:** sub-agent review clean and `uv run poe check` green
  (plus any manual verification). *(The **user-approval** gate is **waived for this
  project** by standing instruction — see step 7; without that waiver, user approval
  is also required.)*
- **Subcommits allowed on the branch, but squash before merge** — one clean commit.
- **Order is fixed: checks → sub-agent review → user review → commit.** `poe check`
  must be green *immediately before each review*; any change re-runs checks (and the
  sub-agent review before returning to the user).
- **`main` is commit-protected, including bookkeeping.** So the tracker + `log.md`
  updates must be done **on the step branch before the fast-forward merge** (step 9)
  — the fast-forward carries them to `main` with the code; there's no way to land a
  post-merge docs commit without a second branch.

## The loop for one step

1. **Select the step.** Take the lowest-numbered step whose status is `todo` in
   the [progress tracker](/build/progress.md)'s **Outstanding (TODO)** section.
   Steps are strictly ordered. (The tracker is split into *Outstanding (TODO)* and
   *Completed (DONE)*: everything not yet merged lives in Outstanding; a step moves
   to Completed only when it lands on `main` — see step 9.)
2. **Branch off `main`.** `git switch main && git pull` (if a remote exists), then
   `git switch -c step-NN-<slug>` (e.g. `step-02-data-model`). In the tracker's
   **Outstanding (TODO)** section, set the step `in-progress` and record the branch
   name (it stays in Outstanding until it merges).
3. **Load the context** (see "Context to load" below) so the work respects the
   established design and the [non-negotiable rules](/reference/folder-structure.md).
4. **Implement test-first.** Follow the [TDD loop](/process/working-agreement.md): failing
   test → minimum implementation → refactor. Make as many **subcommits** as help;
   they will be squashed.
5. **Self-check — the gate for every review.** Run `uv run poe check` (ruff +
   basedpyright + pytest) until green. For a step with no automated test, perform
   the manual verification the [build step](/build/build-order.md) describes and write
   down the result. **No review starts until checks are green.**
6. **Sub-agent review (only on green checks).** Invoke **review sub-agents** on
   the step's diff (`main..HEAD`) — e.g. via the repo's code-review tooling —
   giving each the step's context. If any has comments: address them, **re-run
   `uv run poe check` until green**, then re-review. **Repeat until the reviewers
   return no further comments.** Record `agent-review: passed` in the tracker.

   **The standard panel is up to 4 broad-scope reviewers, each with a distinct
   remit** (do *not* fan out into many narrow single-angle agents beyond these):
   1. **Adversarial — correctness/bugs**: hunt for real defects across the diff.
   2. **Adversarial — correctness/bugs (independent)**: a *second, independent*
      correctness pass prompted to *refute* the change — two adversaries catch
      failure modes one misses.
   3. **Style / reuse / conventions**: quality-practices.md §1–§2 adherence,
      DRY/simplification, dead code, docstring accuracy, test conventions.
   4. **Architecture & software design**: package boundaries, dependency
      direction, seam/abstraction granularity, cohesion/coupling, pattern fit
      (§1.6), extensibility.

   Scale *down* for a small diff: a modest change is fine with one adversarial +
   one style reviewer, and a trivial docs-only change the user directed needs
   none. Reserve the full 4-agent panel for substantive or risky steps. If you
   drive the review through the `/code-review` skill, pick an effort level that
   yields this panel rather than a wide narrow-angle fan-out.
7. **User review (only on green checks + a satisfied sub-agent).** Present the
   step to the **user** (summary of what changed, how it was verified, the diff).
   If the user requests changes: make them, **re-run `uv run poe check` until
   green**, **re-run the sub-agent review until it has no comments**, then return
   to the user. Only a clear user approval advances the step.

   > **Standing waiver (this project, step 4 onward — per the user).** The
   > human-review gate is **waived**: once `poe check` is green and the sub-agent
   > review is clean, squash and fast-forward-merge **autonomously**, no pause for
   > approval. The user may still ask to review any step — the waiver only removes
   > the *mandatory* pause. (This is the canonical statement of the waiver; the
   > tracker just points here.)
8. **Finalize.** With user approval in hand and `uv run poe check` green (and
   manual verification done, if any): **squash** the branch's subcommits into one
   commit using the [commit format](/process/working-agreement.md)
   (`<stage>: <what changed>`, verification in the body). Note its hash
   (`git rev-parse --short HEAD`) — for a fast-forward merge this *is* the hash
   the commit will have on `main`.
9. **Record — still on the step branch, before merging.** In the
   [progress tracker](/build/progress.md), **move the step's row out of
   *Outstanding (TODO)* into the matching *Completed (DONE)* sub-table** (status
   `done`, the hash just noted, the date), and add a dated entry to
   [`log.md`](/log.md) — as one small commit on top of the squashed step commit.
   Moving the row (not just flipping its status) is the bookkeeping the tracker's
   two-section split depends on, so *Outstanding* always lists only what is left.
   Doing this now, rather than after the merge, is required: `main` is
   commit-protected (see hard rules above), so there is no direct way to land a
   tracker update on `main` once the step branch is gone short of opening a whole
   second branch for it.
10. **Merge.** Merge the step branch into `main` via **fast-forward** (linear
    history, no merge commit — which also keeps the `no-commit-to-branch`
    pre-commit hook satisfied), then delete the branch. `main` now has both the
    step commit and the tracker/log commit.
11. **Push.** Push `main` to `origin` (`git push origin main`) so the merged step
    is published. Never force-push `main`. **A `pre-push` hook runs the full
    `uv run poe check` (all static gates + the entire test suite, incl. the
    Docker-backed pgvector tests) before the push is accepted — so the push blocks
    for minutes. Wait for it to complete; don't background it, and don't treat the
    delay as a hang.** If a push seems to time out, it may already have landed:
    `git fetch` and compare `HEAD` vs `origin/main` before retrying (a blind retry
    can hit `remote rejected … cannot lock ref … is at <newsha> but expected
    <oldsha>`, which just means the remote already has your commit).
12. **Next.** Return to step 1 for the following build step.

If a step cannot be completed cleanly, **stop and surface the problem** (mark it
`blocked` in the tracker with a note) rather than committing partial work or
moving on.

## Archiving a completed playbook doc

When the **last** step of a build playbook merges (the whole playbook is done — or
a doc is closed as subsumed), move its doc from `knowledge/build/` into
`knowledge/build/archive/`. This is a standing convention, not a judgement call —
do it the same way every time so it never has to be re-derived:

1. **`git mv`** the doc into `build/archive/` (keeps history).
2. **Deepen the doc's own relative links.** It now sits one directory lower, so
   every `](../…)` link in it gains one more `../` (e.g. a `](../../tests/…)` becomes
   `](../../../tests/…)`). Absolute bundle links (`/build/…`, `/stages/…`, `/log.md`)
   are unaffected.
3. **Rewrite every cross-bundle reference to `/build/archive/<name>.md`.** Grep the
   whole bundle (`grep -rn "build/<name>.md" knowledge`) and fix **all** hits —
   including historical [`log.md`](/log.md) entries and stage/reference docs. The
   already-archived docs (e.g. [`enrichment.md`](/build/archive/enrichment.md)) are
   linked this way; match them.
4. **Move its `index.md` entry** out of *Execution* into the *Archive* section, with
   a description that reflects the finished state.

**Stays in `build/`:** living docs ([`build-order.md`](/build/build-order.md),
[`progress.md`](/build/progress.md), [`open-questions.md`](/build/open-questions.md))
and forward-looking / not-yet-built briefs (analysis notes, feature briefs). Only
*completed* playbooks and step notes are archived.

## Context to load before starting a step

For step N, read, in this order:

1. This workflow document (the loop and the hard rules).
2. The step's entry in the [build order](/build/build-order.md) — and **every concept
   document it links** (the relevant stage doc, [data model](/reference/data-model.md),
   [domain model](/foundations/domain-model.md), etc.). Those links are the step's context.
3. The [non-negotiable structure rules](/reference/folder-structure.md) and the
   [working agreement](/process/working-agreement.md) (TDD table, typing gate, commit
   gate).
4. The [progress tracker](/build/progress.md) — what is already `done`, and any notes
   on `blocked` items.
5. Any [open questions](/build/open-questions.md) the step is tasked with resolving.

## Definition of done for a step

A step is `done` only when **all** are true:

- [ ] Work happened on a `step-NN-<slug>` branch, not `main`.
- [ ] `uv run poe check` is green (ruff, basedpyright, pytest — no regressions).
- [ ] Manual verification performed and recorded, if the step has no automated test.
- [ ] The review sub-agent has no remaining comments.
- [ ] The user has explicitly approved.
- [ ] Subcommits squashed into one `<stage>: <what changed>` commit with the
      verification in the body.
- [ ] [Progress tracker](/build/progress.md) updated **on the step branch** — the
      step's row **moved from *Outstanding (TODO)* to *Completed (DONE)*** with its
      hash — and a dated [`log.md`](/log.md) entry added, in a commit on top of the
      squashed step commit (not after merging — `main` can't take a direct commit).
- [ ] Branch merged into `main`, branch deleted, and `main` pushed to `origin`.

---
type: Working Agreement
title: Working agreement
description: Test-driven development, static typing, the git commit gate, and the local workflow commands.
tags: [process, tdd, typing, git, tooling]
timestamp: 2026-07-12
---

# Working agreement

## Be concise

Applies to all work here — docs, `log.md`, commit bodies, review comments, and
chat. Say what changed, why, and what's left; then stop. Prefer bullets and
tables over prose, cut restatement and hedging, and don't narrate the process.
If it reads like a diary, trim it.

## Test-driven development

Every stage is developed test-first:

1. **Write the failing test.** Define the stage's contract as a pytest test
   against fixture data before touching the implementation. Run it, watch it fail
   for the right reason.
2. **Write the minimum implementation** to make it pass.
3. **Refactor** with the test as a safety net.

What "testable" means per stage type:

| Stage type | How it's tested |
|---|---|
| Pure logic (clustering, selection, segment matching) | Plain pytest unit tests over **in-memory SQLite** + synthetic `Photo` rows, with vector similarity stubbed. Fast, deterministic, no mocks. Highest-value tests — write them thoroughly. |
| Anything touching `pgvector` | A **Postgres test container** (`testcontainers-python`). Slower, so keep these few and focused on the query itself, not the surrounding logic. |
| Immich client | Recorded HTTP fixtures (`respx` or `vcrpy`), not the live server. |
| PydanticAI agents | PydanticAI's `TestModel` / `FunctionModel` — assert the output model validates and bounds are enforced, without hitting the Claude API. |
| Image ops (blur score, histogram match) | Small committed fixture images with known properties (a deliberately blurred one, a deliberately overexposed one). |
| darktable render, Docker, Lulu submission | Not meaningfully unit-testable. **Manually verified** — see below. |

Some steps have no sensible automated test (does the Docker image actually build
and run? does darktable actually produce a corrected JPEG?). Those steps are
"done" when **manually verified**, with the verification written into the commit
body. Don't fake a test just to have one; don't skip the verification either.

The table above governs *what layer* each stage is tested at. How tests are
*organized* — class-based groups, parametrization within them, and factory
fixtures scoped to the tests that need them — is standardized in
[quality practices](/process/quality-practices.md), alongside how the production code
those tests exercise is shaped (stage classes with constructor-injected
dependencies).

## Static typing

The codebase is heavily typed — SQLAlchemy 2.0 `Mapped[...]` columns, Pydantic v2
models, PydanticAI structured output — so a static type checker is a first-class
gate, not an afterthought.

- **`basedpyright`** is the checker (a pip-installable fork of pyright — no Node
  needed, `uv add --dev basedpyright`). SQLAlchemy 2.0 `Mapped[]` and Pydantic v2
  both type-check cleanly under it with no plugin.
- Run in a **reasonably strict** mode (error on type errors; flag missing
  annotations on public functions). Configure in `pyproject.toml` under
  `[tool.basedpyright]`.
- It runs as part of the commit gate (`uv run poe check`) and belongs in CI once
  pipelines exist.

> **Planned migration to `ty`.** Astral's `ty` (same family as `uv`/`ruff`) is the
> intended long-term checker, held at preview maturity for now (its SQLAlchemy/Pydantic
> handling is still stabilizing). Start on `basedpyright`; revisit `ty` at stable
> release. The swap is cheap (a dev dep + one line), so **nothing downstream may assume
> a specific checker**.

## Git workflow

The *delivery* loop (branch per step, sub-agent then user review, squash, merge, push)
is the [implementation workflow](/process/implementation-workflow.md). **Never commit to
or work on `main`.** This section defines the *quality bar* a step clears before it's
finalized — bundled into `uv run poe check`, the one command that gates a commit:

- `ruff check .` + `ruff format --check .` (ruff includes mccabe `C90`, ceiling 5)
- `basedpyright` — no errors
- `xenon` — the complexity gate, **A across the board** (see
  [quality practices](/process/quality-practices.md) §2.7)
- `pytest` — *all* tests, no regressions
- If the step has no automated test: the manual verification is performed + recorded.

- Commit message format: `<stage>: <what changed>`, verification in the body:

  ```
  cluster: assign photos to segments and detect scenes by time gap

  Falls back to pure time-gap clustering when an album has no segments.
  ruff + basedpyright + pytest pass (17 tests). Verified against honeymoon
  fixture: 2,847 photos → 21 segments → 68 scenes, no unassigned photos.
  ```

- If a step can't be completed cleanly, **do not commit it and move on**. Stop
  and surface the problem.

### Commit body vs. `log.md` — division of labour

Two records, deliberately kept separate; neither restates the other.

| Record | Owns | Granularity |
|---|---|---|
| **Commit body** | The *mechanical* per-diff record — what changed and why in **that** commit, plus the verification (test count / manual check). | Exactly one per commit. |
| [**`log.md`**](/log.md) | The *narrative/decision* layer git can't hold: records that span **several** commits or **none at all** — proposals, decisions, open-question resolutions, review outcomes, and the deferral list. Cross-links into the [`knowledge/`](/index.md) bundle. | One entry per meaningful *change of state*, not per commit. |

Consequences that keep the two from drifting into copies of each other:

- **`log.md` points *into* git; it does not duplicate it.** Reference a commit by
  its SHA (`commit ba6c1bf`) rather than re-listing file moves or exact test
  counts — those live in the commit and the diff. This also means the log survives
  a change of granularity: an entry may cite several SHAs, or none.
- **Rationale that outlives one diff goes in the log**; rationale scoped to a
  single diff goes in that commit's body. When in doubt, ask "does this make sense
  to someone reading only this commit?" — if yes, commit body; if it only makes
  sense across steps, `log.md`.
- **Keep entries tight** (see [Be concise](#be-concise)): a few bullets per state
  change — decision, rationale, SHA(s), deferral. Not a per-commit replay.
- **Do not fold the log into commit history** (e.g. by rewriting messages). The
  log's entries don't map 1:1 to commits, its markdown links only resolve inside
  the bundle, it stays editable for de-staling, and a history rewrite would
  invalidate the very SHAs the log cites.

### Pre-commit hooks (local enforcement)

A `.pre-commit-config.yaml` (installed at [scaffold](/build/build-order.md) via
`uv run pre-commit install`) enforces the basics automatically on every commit —
including subcommits on a step branch — so nothing slips through:

- **`no-commit-to-branch`** — blocks direct commits to `main`, backing up the
  "never commit to `main`" rule at the tooling level. (Landing a step is a
  fast-forward merge, which creates no commit and so passes the hook.)
- **ruff** — `ruff check --fix` (incl. the mccabe `C90` complexity check, ceiling 5) and `ruff format`.
- **basedpyright** — the static type check.
- **xenon** — the complexity/maintainability gate; a fast, import-free static
  metric check, so it runs per-commit with the other static gates rather than in
  the slower `pytest`/`poe check` tier.

The full `pytest` suite is *not* run per-commit (too slow for fast subcommits); it
runs in `uv run poe check` before a step is finalized, and in CI. Optionally add
it as a `pre-push` hook.

> **Landmine — ruff formatter under `py314`.** `ruff` 0.15.21's formatter with
> `target-version = "py314"` rewrites a valid `except (A, B):` into the invalid
> paren-less form. Use separate `except` clauses, or `# fmt: skip` on that line.
> (Discovered 2026-07-13 — see [log.md](/log.md).)

## Local workflow commands

Common workflow actions are encoded as `poethepoet` tasks in `pyproject.toml`
(`[tool.poe.tasks]`), so they're triggerable from the CLI via `uv run poe <task>`
without memorizing raw invocations. At minimum:

| Command | What it does |
|---|---|
| `uv run poe lint` | `ruff check .` |
| `uv run poe format` | `ruff format .` (write) |
| `uv run poe typecheck` | `basedpyright` |
| `uv run poe complexity` | `xenon` complexity/maintainability gate (absolute A / modules A / average A — CC ≤ 5 everywhere) |
| `uv run poe metrics` | non-gating radon report: cyclomatic complexity + maintainability index per module |
| `uv run poe test` | `pytest` |
| `uv run poe check` | lint + `ruff format --check .` + typecheck + complexity + test — **the commit gate** |
| `uv run poe up` / `uv run poe down` | `docker compose up` / `down` (full local stack) |
| `uv run poe migrate` | `alembic upgrade head` |
| `uv run poe makemigration -- -m "..."` | `alembic revision --autogenerate` |
| `uv run poe build -- ./photos` | one-shot: folder → Immich album → `album.yml` → run (add `--manifest` to fan a tree into N books) |
| `uv run poe build -- --no-ingest --album honeymoon-2026` | skip ingest; run the pipeline flow for one existing album |

The pipeline is also exposed as a console entry point via `[project.scripts]`
(`photobook-generator = "album.cli:main"`), so `uv run photobook-generator build
--no-ingest --album honeymoon-2026` works directly; `poe build` is the same thing
wrapped for symmetry with the other tasks.
This is the canonical command table — the README only links here rather than
duplicating it, so there's nothing else to keep in sync.

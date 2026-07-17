---
type: Working Agreement
title: Working agreement
description: Test-driven development, static typing, the git commit gate, and the local workflow commands.
tags: [process, tdd, typing, git, tooling]
timestamp: 2026-07-12
---

# Working agreement

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

> **Planned migration to `ty`.** Astral's `ty` (same toolchain family as `uv` and
> `ruff`) is the intended long-term checker — it's Rust-fast and shares config
> idioms with the rest of the stack. It's held at preview maturity for now, and
> its handling of the advanced SQLAlchemy/Pydantic typing this project leans on is
> still stabilizing, so we don't build the foundation on it yet. Start on
> `basedpyright`; revisit `ty` once it reaches a stable release. The swap is
> deliberately cheap — a dev dependency plus one line in `poe check` and CI — so
> **nothing downstream may assume a specific checker**.

## Git workflow

Delivery follows the [implementation workflow](/process/implementation-workflow.md): one
build step per branch, reviewed by a sub-agent and then the user, squashed, and
merged. **Never commit to or work on `main`.** This section defines the quality
bar a step must clear before it can be finalized.

- `git init` at step 0. Each build step lands on `main` as **one squashed commit**
  via its branch merge — not one big dump at the end, and never a direct commit to
  `main`.
- A step is "successfully completed" only when **all** of:
  - `ruff check .` and `ruff format --check .` pass (ruff includes the mccabe
    `C90` complexity smell check, ceiling 5)
  - `basedpyright` reports no errors (static type check)
  - `xenon` passes the complexity/maintainability gate — **A across the board**:
    no block worse than A (CC ≤ 5), no module averaging worse than A,
    whole-codebase average A (see
    [quality practices](/process/quality-practices.md))
  - `pytest` passes — *all* tests, not just the new ones (no regressions)
  - If the step has no automated test: the manual verification described in that
    step has been performed, and its result noted
  - The review sub-agent has no remaining comments, **and the user has approved**
    (see the [implementation workflow](/process/implementation-workflow.md))

  The first four are bundled into `uv run poe check` — the one command that gates
  a commit.
- Commit message format: `<stage>: <what changed>`, verification in the body:

  ```
  cluster: assign photos to segments and detect scenes by time gap

  Falls back to pure time-gap clustering when an album has no segments.
  ruff + basedpyright + pytest pass (17 tests). Verified against honeymoon
  fixture: 2,847 photos → 21 segments → 68 scenes, no unassigned photos.
  ```

- If a step can't be completed cleanly, **do not commit it and move on**. Stop
  and surface the problem.

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
| `uv run poe run -- --album honeymoon-2026` | run the pipeline flow for one album |

The pipeline is also exposed as a console entry point via `[project.scripts]`
(`photobook-generator = "album.cli:main"`), so `uv run photobook-generator run
--album honeymoon-2026` works directly; `poe run` is the same thing wrapped for
symmetry with the other tasks.
Keep this table and the README's command list in sync when tasks change.

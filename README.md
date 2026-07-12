# Photo Album Pipeline

A batch pipeline that turns a raw dump of photos into a curated, enhanced,
captioned, print-ready photo book, submitted to a print API — with minimal
manual work. It's **event-type agnostic**: a trip, a wedding, a baby's first
year, a festival are all just *albums* (an `Album` with optional, named
`Segment`s).

The [`knowledge/`](./knowledge/index.md) directory is this project's source of
truth — domain model, architecture, data model, and the step-by-step build
order. [`AGENTS.md`](./AGENTS.md) orients coding agents working in this repo.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (manages the Python install and virtualenv
  — no separate Python setup needed; the project targets Python 3.14)
- Docker (for the local stack: Postgres, Immich, Prefect — brought up starting
  at build step 1)

## Getting started

```sh
uv sync                    # creates .venv/ and installs dependencies
uv run pre-commit install  # installs the git hooks (no-commit-to-branch, ruff, basedpyright)
```

Always run project tools through `uv run ...` (or activate `.venv/` first) so
they resolve against the project's virtualenv, not a system Python.

## Local stack

```sh
uv run poe up      # docker compose up: app + Postgres (pgvector) + Immich + Prefect
uv run poe down    # docker compose down
uv run poe migrate # alembic upgrade head
```

`poe up`/`poe down`/`poe migrate` become functional once
[build steps 1 and 2](./knowledge/build-order.md) land the compose file and the
migrations; until then they will error.

## Workflow commands

| Command | What it does |
|---|---|
| `uv run poe lint` | `ruff check .` |
| `uv run poe format` | `ruff format .` (write) |
| `uv run poe typecheck` | `basedpyright` |
| `uv run poe test` | `pytest` |
| `uv run poe check` | lint + `ruff format --check .` + typecheck + test — the commit gate |
| `uv run poe up` / `uv run poe down` | bring the local stack up / down |
| `uv run poe migrate` | `alembic upgrade head` |
| `uv run poe run -- --album <slug>` | run the pipeline flow for one album |

The pipeline is also a console entry point: `uv run album run --album <slug>`.

## Contributing

See [`AGENTS.md`](./AGENTS.md) and
[`knowledge/implementation-workflow.md`](./knowledge/implementation-workflow.md)
for how a build step is branched, developed test-first, reviewed, and merged.
`main` is protected by a `no-commit-to-branch` pre-commit hook — work always
happens on a `step-NN-<slug>` branch.

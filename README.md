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

## Run a pipeline end-to-end

1. Add `data/albums/<slug>/album.yml` (see
   [Album definition](./knowledge/album-definition.md)) with a real
   `immich_album_id` from Immich's web UI, and set the `.env` credentials it
   needs (`IMMICH_API_KEY`, `ANTHROPIC_API_KEY`, `LULU_CLIENT_KEY`/
   `LULU_CLIENT_SECRET` — see `.env.example`).
2. `uv run poe up` (or `docker compose up -d db prefect-server` for just the
   two services a run needs, skipping Immich's heavier stack if it's already
   running elsewhere), then `uv run poe migrate`.
3. `uv run album run --album <slug>` (equivalently `uv run poe run --
   --album <slug>`). Watch it in Prefect's UI at `http://localhost:4200`.

Every dependency `flow.py` wires is real except one, deliberately deferred
since [build step 6](./knowledge/progress.md): the LAION-style aesthetic
predictor + CLIP embedder (`open_clip`/`torch` — a heavy, not-yet-added
dependency). A run still completes without it — `select`'s MMR has a
documented fallback for missing `aesthetic_score`/`clip_embedding` — just
with degraded curation quality until that's wired.

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

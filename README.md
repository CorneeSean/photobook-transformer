# Photo Album Pipeline

A batch pipeline that turns a raw dump of photos into a curated, enhanced,
captioned, print-ready photo book — with minimal manual work. It's
**event-type agnostic**: a trip, a wedding, a baby's first year, a festival are
all just *albums* (an `Album` with optional, named `Segment`s).

The [`knowledge/`](./knowledge/index.md) bundle is this project's source of
truth — domain model, architecture, data model, and the step-by-step build
order. [`AGENTS.md`](./AGENTS.md) orients coding agents working in this repo.

## Quick start path

Photos don't go into the pipeline directly — they first live in **Immich** (a
self-hosted photo library that runs locally in Docker). The flow is: **your
folder → Immich album → pipeline → `index.html`.**

```sh
# 0. Install — once
uv sync && uv run pre-commit install
cp .env.example .env

# 1. Start the local stack (Immich + Prefect + Postgres)
uv run poe up
uv run poe migrate
```

Open `http://localhost:2283`, create the admin account (first visit only), then
grab an API key (*Account Settings → API Keys → New*) and put it in `.env` as
`IMMICH__API_KEY`.

```sh
# 2. Generate the album — one command does upload, Immich album creation, and the run
uv run photobook-generator generate ./path/to/photos
```

Your book lands at `data/albums/<slug>/book/index.html` — open it in any
browser. `ANTHROPIC_API_KEY` is optional (only for the opt-in `--captioner
claude` upgrade); the default run is fully offline. `generate` also takes
`--slug`, `--title`, `--timezone`, `--force`, and `--source-document
schedule.pdf` (extracts `segments` from a trip itinerary / run-sheet / line-up).
To hand-author `segments`/`lead_faces` instead, write `data/albums/<slug>/album.yml`
yourself and run `uv run photobook-generator run --album <slug>` — see
[Album definition](./knowledge/reference/album-definition.md) and
[Single-command ingestion](./knowledge/build/single-command-ingestion.md).

## Architecture

Batch pipeline over Immich + Postgres, orchestrated by Prefect, PydanticAI for
the (optional) LLM call — fully offline by default.

→ [`knowledge/foundations/architecture.md`](./knowledge/foundations/architecture.md)
→ [`knowledge/foundations/domain-model.md`](./knowledge/foundations/domain-model.md)
→ [`knowledge/foundations/pipeline-execution.md`](./knowledge/foundations/pipeline-execution.md)
(companion diagram: `pipeline-execution.html`)

## Pipeline stages

Ten stages, `sync` → `fulfill`, one concept per stage.

→ [`knowledge/stages/index.md`](./knowledge/stages/index.md)

## Reference

Album definition, data model, tech stack, folder structure.

→ [`knowledge/reference/album-definition.md`](./knowledge/reference/album-definition.md)
→ [`knowledge/reference/data-model.md`](./knowledge/reference/data-model.md)
→ [`knowledge/reference/tech-stack.md`](./knowledge/reference/tech-stack.md)
→ [`knowledge/reference/folder-structure.md`](./knowledge/reference/folder-structure.md)

## Process & workflow commands

TDD, typing, the git commit gate, the per-step implementation loop, and the
full `poe` task table.

→ [`knowledge/process/working-agreement.md`](./knowledge/process/working-agreement.md)
→ [`knowledge/process/implementation-workflow.md`](./knowledge/process/implementation-workflow.md)
→ [`knowledge/process/quality-practices.md`](./knowledge/process/quality-practices.md)

## Build status

What's done, what's deferred, and why.

→ [`knowledge/build/progress.md`](./knowledge/build/progress.md)
→ [`knowledge/build/build-order.md`](./knowledge/build/build-order.md)

## Ordering a physical copy

The default `fulfill` stage writes a self-contained HTML book (`HtmlPrinter`).
To order a physical copy via [Lulu](https://api.lulu.com)'s Print API instead,
inject `LuluPrinter` and set the `LULU__*` keys in
[`.env.example`](./.env.example).

→ [`knowledge/stages/fulfill.md`](./knowledge/stages/fulfill.md)

## Contributing

`main` is protected by a `no-commit-to-branch` hook — work always happens on a
`step-NN-<slug>` branch, TDD, reviewed, squashed, and merged.

→ [`AGENTS.md`](./AGENTS.md)
→ [`knowledge/process/implementation-workflow.md`](./knowledge/process/implementation-workflow.md)

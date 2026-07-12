---
type: Reference
title: Tech stack
description: The chosen tool for each concern, from language to print fulfillment.
tags: [reference, tooling, dependencies]
timestamp: 2026-07-12
---

# Tech stack

| Concern | Choice |
|---|---|
| Language | Python 3.14 |
| Dependency management | uv |
| Lint + format | ruff |
| Static type checking | basedpyright (migrate to Astral `ty` once it's stable) |
| Testing | pytest (+ `respx`/`vcrpy` for HTTP, `testcontainers` for Postgres) |
| Task runner | poethepoet — workflow commands as `uv run poe <task>` |
| VCS | git — one branch per build step, squash-merged to `main` (never commit to `main`) |
| Git hooks | pre-commit — `no-commit-to-branch` (protects `main`) + ruff + basedpyright |
| App image | Docker (multi-stage; `uv sync` in build stage) |
| Local environment | docker-compose (app + Postgres + Immich stack + Prefect server) |
| Photo storage / ingest / dedup | Immich (self-hosted) |
| Pipeline metadata store | **Postgres + `pgvector`**, via SQLAlchemy ORM |
| Migrations | alembic |
| Album definitions | YAML (`album.yml` per album) |
| Orchestration | Prefect 3 (local server) |
| LLM integration | PydanticAI (Claude as provider) |
| Embeddings | `open_clip` (ViT-L/14) — only if Immich doesn't expose its own |
| Vector similarity | `pgvector` (in-database, indexed) |
| Aesthetic scoring | LAION aesthetic predictor |
| Blur / image metrics | OpenCV |
| Spatial clustering | scikit-learn (DBSCAN) — only when GPS is present |
| Tone rendering | `darktable-cli`, driven by XMP sidecars |
| Color matching | scikit-image (`match_histograms`) |
| Layout → PDF | Jinja2 + WeasyPrint (fallback: headless Playwright) |
| Print fulfillment | Lulu Print API |

See [Working agreement](/working-agreement.md) for how the tooling choices are
exercised (typing gate, commit gate, `uv run poe` tasks) and
[Architecture](/architecture.md) for why Postgres, Prefect, and PydanticAI.

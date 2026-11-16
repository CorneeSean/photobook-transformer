---
type: Architecture Decision
title: Architecture pattern
description: A resumable batch pipeline over Immich + Postgres, orchestrated by Prefect, with PydanticAI for LLM calls.
tags: [architecture, prefect, postgres, immich, pydantic-ai, docker]
timestamp: 2026-07-12
---

# Architecture pattern

A batch pipeline, not a service. It runs on demand per album (and gets re-run
while tuning), not continuously — so no event bus, no queue, no streaming.

**Immich** (self-hosted) is the ingestion + storage + dedup backbone, shared
across all albums. Raw photos live there and never move; Immich runs its own ML
jobs (duplicate detection, smart-search embeddings, face detection) on import.
Each album maps to one **Immich album**, which is how [`sync`](../stages/sync.md)
knows which assets belong where.

**SQLAlchemy ORM over Postgres** is the source of truth for everything *our*
pipeline computes — one row per photo, scoped by `album_id`, with columns filled
in incrementally as stages run. One database serves all albums. It also makes
every stage resumable: a crash at photo 1,847 doesn't mean restarting from zero.

## Why Postgres and not SQLite

SQLite is the obvious default, but it's the wrong choice here:

- **Concurrency fights the orchestration.** SQLite allows one writer; Prefect's whole
  value is fanning out across thousands of photos with per-item retry. The workarounds
  (WAL, batching) shape task granularity around the DB and *defeat* failure isolation
  (one failure loses the batch).
- **No infrastructure is being avoided.** Immich's compose stack already runs Postgres,
  so the usual "why stand up a DB server" argument doesn't apply.
- **`pgvector` turns a workaround into a feature.** Our own CLIP embeddings need indexed
  similarity search — native in `pgvector`, brute-force numpy BLOBs in SQLite — which
  both [`select`](../stages/select.md) (MMR) and [`cluster`](../stages/cluster.md) want.
- "Start on SQLite, migrate if it hurts" is a trap: the pain would surface around step
  14 with the flow fully wired.

**Run our own Postgres** in the compose file, separate from Immich's; our alembic
migrations never touch Immich's schema. SQLAlchemy still earns its place for
**portability + testability** — pure-logic stages unit-test against **in-memory
SQLite** (they don't touch `pgvector`).

## Idempotent stages

Each stage is an **idempotent function**: reads from the DB (+ the Immich API as
needed), writes results back, never mutates originals. Re-running a stage on an
already-processed album must be safe and produce the same result.

## Orchestration — Prefect from the start

Justified by failure profile, not scale: stages hit external APIs (Immich,
Claude) across thousands of photos, so retries with backoff, per-photo failure
isolation (one corrupt file shouldn't kill a 3,000-item run), and a visible run
history are worth the setup cost. Each stage becomes a `@task`; the pipeline is
one `@flow` taking `album_slug`. A local Prefect server is enough — no cloud, no
deployments.

> Note: in Prefect 3, run a parameterized flow by importing and calling it
> (`pipeline(album_slug="honeymoon-2026")`), typically from the `cli/` package. There is no
> `prefect run --param`; that's Prefect 1 syntax.

## LLM integration — PydanticAI, not the raw Anthropic SDK

The AI stages need *structured, bounded* output (numeric tone deltas; a caption
string). PydanticAI gives a validated Pydantic output model with automatic retry
on malformed output, instead of hand-parsing JSON from a text response. It also
provides `TestModel`, which is what makes these stages unit-testable at all.

**One shared analysis, one fetch — not two.** [`enhance`](../stages/enhance.md) and
[`caption`](../stages/caption.md) both need a downscaled thumbnail of the same *selected*
photos, and the fetch is the expensive part. A single collaborator
([`PhotoAnalyzer`](../stages/enhance.md)) produces *both* the bounded tone deltas *and*
the caption from one fetch, **once per selected photo** — persisted and idempotent
(skipped when the row is populated), grounded on whatever context the album has
(segment name/place, people, capture time, `Album.meta`), regardless of which stage
triggers it first.

As of **step 29** ([local-analysis.md](../build/archive/local-analysis.md)) both halves are
**classical and offline by default** — `ClassicalToneEstimator` (CV) + `TemplateCaptioner`
(metadata templating), behind injectable `ToneEstimator`/`Captioner` seams — so a default
run needs **no external API and no key**. `ClaudeCaptioner` is the opt-in rich-caption
upgrade (selectable per run), mirroring the `Printer` `HtmlPrinter`/`LuluPrinter` split.

## Containers

**Docker for the app image; docker-compose for the local environment.** The app
gets a multi-stage `Dockerfile` (build stage runs `uv sync`; runtime stage
carries the venv plus system deps like `darktable-cli`), so it behaves
identically on any machine. `docker-compose.yml` composes that app image with
Immich's own stack and a Prefect server, so a fresh checkout needs nothing
installed but Docker.

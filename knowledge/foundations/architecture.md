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
Each album maps to one **Immich album**, which is how [`sync`](/stages/sync.md)
knows which assets belong where.

**SQLAlchemy ORM over Postgres** is the source of truth for everything *our*
pipeline computes — one row per photo, scoped by `album_id`, with columns filled
in incrementally as stages run. One database serves all albums. It also makes
every stage resumable: a crash at photo 1,847 doesn't mean restarting from zero.

## Why Postgres and not SQLite

SQLite is the obvious default for a single-user batch tool, and it was the
original choice here. It's the wrong one, for three reasons:

1. **The concurrency model fights the orchestration model.** SQLite permits one
   writer at a time. Prefect's entire value in this design is fanning out across
   thousands of photos with per-item retry and failure isolation — exactly
   SQLite's worst access pattern. The workarounds (WAL mode, tasks returning
   instead of writing, batching writes in the parent) all mean shaping task
   granularity around the database's limitation instead of around what makes
   sense for retries. Worse, batched writes *defeat the isolation*: one failure
   takes the whole batch's work with it.
2. **There's no infrastructure being avoided.** The usual case for SQLite is
   "why stand up a database server." But Immich's compose stack already runs
   Postgres. Choosing the constrained option to dodge a cost we're already paying
   is a bad trade.
3. **`pgvector` turns a workaround into a feature.** If Immich doesn't expose its
   CLIP embeddings and we compute our own, SQLite means storing vectors as opaque
   BLOBs and doing brute-force cosine similarity in numpy. Postgres + `pgvector`
   gives indexed similarity search natively — which both [`select`](/stages/select.md)
   (MMR) and [`cluster`](/stages/cluster.md) (visual scene refinement) want.

The "start on SQLite, migrate if it hurts" plan is a trap: the ORM makes that
swap cheap in *code*, but you'd discover the pain around step 14, with the flow
fully wired, and end up debugging a datastore migration and a pipeline
simultaneously.

**Run our own Postgres service** in the compose file — a separate instance (or at
minimum a separate database) from Immich's. Our alembic migrations should never
be anywhere near Immich's schema.

SQLAlchemy still earns its place: it now buys **portability and testability**
rather than an escape hatch we were planning to use. Unit tests for the
pure-logic stages run against **in-memory SQLite** for speed — those stages don't
touch `pgvector` and don't care.

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
> (`pipeline(album_slug="honeymoon-2026")`), typically from `cli.py`. There is no
> `prefect run --param`; that's Prefect 1 syntax.

## LLM integration — PydanticAI, not the raw Anthropic SDK

The AI stages need *structured, bounded* output (numeric tone deltas; a caption
string). PydanticAI gives a validated Pydantic output model with automatic retry
on malformed output, instead of hand-parsing JSON from a text response. It also
provides `TestModel`, which is what makes these stages unit-testable at all.

**One shared analysis, one fetch — not two.** [`enhance`](/stages/enhance.md) and
[`caption`](/stages/caption.md) both need a downscaled thumbnail of the same
*selected* photos — and fetching that image is the expensive part. So a **single
collaborator** ([`PhotoAnalyzer`](/stages/enhance.md)) produces *both* the bounded tone
deltas *and* the caption from one fetch, **once per selected photo**. Its result is
persisted (tone-delta columns + `caption`) and idempotent — skipped when the row is
already populated. `enhance` and `caption` each consume their slice rather than each
re-fetching. Because the caption needs grounding, the analysis assembles whatever
context the album has (segment name/place, place, people, capture time, `Album.meta`)
and passes it in, regardless of which stage triggers it first.

As of **step 29** ([local-analysis.md](/build/local-analysis.md)) both halves are
**classical and offline by default** — tone from a `ClassicalToneEstimator` (CV) and
the caption from a `TemplateCaptioner` (metadata templating) — behind injectable
`ToneEstimator`/`Captioner` seams, so a default run needs **no external API and no
key**. A `ClaudeCaptioner` is the opt-in "rich caption" upgrade (selectable per run),
mirroring the `Printer` boundary's `HtmlPrinter`/`LuluPrinter` split.

## Containers

**Docker for the app image; docker-compose for the local environment.** The app
gets a multi-stage `Dockerfile` (build stage runs `uv sync`; runtime stage
carries the venv plus system deps like `darktable-cli`), so it behaves
identically on any machine. `docker-compose.yml` composes that app image with
Immich's own stack and a Prefect server, so a fresh checkout needs nothing
installed but Docker.

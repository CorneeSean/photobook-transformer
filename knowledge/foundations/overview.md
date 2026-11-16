---
type: Overview
title: Photo Album Pipeline — Overview
description: What the pipeline does and the ground rules for building it.
tags: [overview, goal, scope]
timestamp: 2026-07-12
---

# Overview

Turn a raw dump of photos into a curated, enhanced, captioned photo book,
organized into a sensible narrative order, and submitted to print via an API —
with minimal manual work.

The pipeline is **event-type agnostic**. A trip, a wedding, a baby's first year,
a festival, a year of family photos — all are just *albums*: a bounded set of
photos that get clustered, culled, corrected, captioned, and bound. The first
album through it is a 21-day honeymoon (~2,000–3,000 photos, no GPS in EXIF), but
nothing about "trips" is baked into the schema or the stages.

Build all glue code from scratch. Use existing pretrained models (train nothing)
and one existing self-hosted app (Immich) rather than reimplementing
storage/dedup.

## Where to go next

* [Domain model](domain-model.md) — the `Album` + `Segment` abstraction that
  makes the pipeline generic.
* [Architecture](architecture.md) — batch pipeline, datastore, orchestration,
  and LLM decisions with their rationale.
* [Working agreement](../process/working-agreement.md) — TDD, typing, git workflow, and
  the local commands.
* [Build order](../build/build-order.md) — the step-by-step plan, each ending in a
  commit.
* [Pipeline stages](../stages/) — one concept per stage.

---
type: Pipeline Stage
title: select
description: Diversity-aware best-of-scene via MMR; the single most important stage to test thoroughly.
tags: [stage, custom, select, mmr, pgvector]
timestamp: 2026-07-12
---

# `select` — custom

Diversity-aware best-of-scene, using **MMR** (maximal marginal relevance): within
each scene, greedily pick the highest `aesthetic_score` photo, then skip any
candidate whose CLIP cosine similarity to an already-picked photo exceeds a
threshold. Also skip anything Immich flagged as a duplicate. Result: the best
shots, without five near-identical "good" ones.

Pure logic over data already in the [DB](/data-model.md) — **the single most
important stage to test thoroughly.** The `pgvector` similarity query sits behind
a substitutable interface (`db/similarity.py`) so the MMR logic is testable on
in-memory SQLite with similarity stubbed, while the real query is covered against
a Postgres testcontainer (see [build step 8](/build-order.md)).

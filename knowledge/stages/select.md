---
type: Pipeline Stage
title: select
description: Diversity-aware best-of-scene via MMR; the single most important stage to test thoroughly.
tags: [stage, custom, select, mmr, pgvector]
timestamp: 2026-07-12
---

# `select` — custom

Diversity-aware best-of-scene, using **MMR** (maximal marginal relevance): within
each scene, rank by an **effective score** (highest first), then greedily pick,
skipping any candidate whose CLIP cosine similarity to an already-picked photo
exceeds a threshold, or that Immich flagged as a duplicate. Result: the best
shots, without five near-identical "good" ones.

**People/lead awareness** (enrichment step 18 — see
[Immich enrichment](../build/archive/enrichment.md)) layers on top and reduces to plain MMR for a
face-free scene (byte-for-byte). The **effective score** is `aesthetic_score` plus small
tunable boosts — `group_boost` per recognized person and `lead_boost` per lead
(`Person.is_lead`) — so a lead outranks the group tie-break; being small, they only
reorder *comparable* photos. Plus two coverage postures:

- **Lead guaranteed coverage:** after the greedy pass, every lead in a scene gets ≥1
  picked photo — admitted even as a near-duplicate of a *non*-lead (skipping only an
  exact dup-group repeat), so a couple sharing a frame are each covered.
- **Strict on non-leads:** no coverage phase — a non-lead seen only in near-duplicates
  stays unselected rather than padding the book.

**Rating awareness** (enrichment step 20) folds Immich's manual `rating` (−1…5) into the
same effective score as a small per-star `rating_boost` (5 stars ≈ +0.10) — a soft
tie-break that reorders comparable photos (a clearly better shot still wins), inert for
an unrated album (`rating` NULL/0 ⇒ byte-for-byte). Lead coverage is separate, so it
never drops a lead.

Pure logic over data already in the [DB](../reference/data-model.md) — **the single most
important stage to test thoroughly.** The `pgvector` similarity query sits behind
a substitutable interface (`db/similarity.py`) so the MMR logic is testable on
in-memory SQLite with similarity stubbed, while the real query is covered against
a Postgres testcontainer (see [build step 8](../build/build-order.md)).

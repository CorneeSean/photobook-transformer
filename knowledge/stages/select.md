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

**People/lead awareness** (Immich faces, step 17; enrichment step 18 — see
[Immich enrichment](/build/enrichment.md)) layers on top, and reduces to plain
MMR when a scene has no recognized faces (byte-for-byte):

- The effective score is `aesthetic_score` plus small, tunable boosts:
  `group_boost` per recognized person (a group shot edges out an incidental one
  only when aesthetic scores are close) and `lead_boost` **per lead** in the
  photo (`Person.is_lead`, from `album.yml`'s `lead_faces`), so a lead outranks
  the group tie-break and a frame with more leads (a whole couple) outranks one
  with fewer. Being small, the boosts only reorder *comparable* photos — a
  distinct "original" scene is still picked on its own merits.
- **Lead guaranteed coverage:** after the greedy pass, every lead present in a
  scene gets at least one picked photo — a lead is admitted even when it's a
  near-duplicate of a *non*-lead (never culled for the leads' sake), skipping
  only an exact Immich dup-group repeat. Runs once per uncovered lead, so a
  couple sharing a near-duplicate frame are each covered.
- **Strict on everyone else:** there is deliberately no coverage phase for
  non-leads — honoring the near-dup guard strictly (chosen posture), a non-lead
  seen only in near-duplicates stays unselected rather than padding the book.

**Rating awareness** (Immich's manual `rating`, −1…5; enrichment step 20) folds
into the same effective score as a small per-star `rating_boost` — honoring the
user's own curation (a starred photo edges out comparable frames, a rejected one
is pushed below them). A *soft* tie-break on the people-boost scale (a full 5
stars ≈ +0.10) that reorders comparable photos — a clearly better shot still
wins — and inert for an unrated album (`rating` NULL/0 ⇒ byte-for-byte). Lead
coverage is guaranteed separately, so a rating boost never drops a lead.

Pure logic over data already in the [DB](/reference/data-model.md) — **the single most
important stage to test thoroughly.** The `pgvector` similarity query sits behind
a substitutable interface (`db/similarity.py`) so the MMR logic is testable on
in-memory SQLite with similarity stubbed, while the real query is covered against
a Postgres testcontainer (see [build step 8](/build/build-order.md)).

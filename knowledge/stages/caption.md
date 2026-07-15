---
type: Pipeline Stage
title: caption
description: Grounded captions from the shared analysis call — as grounded as the album's metadata allows.
tags: [stage, ai, caption, grounding]
timestamp: 2026-07-12
---

# `caption` — AI

Consumes the caption produced by the **shared analysis call** described under
[`enhance`](./enhance.md) (one agent, one round-trip per selected photo, producing
tone deltas and a caption together). If `enhance` already ran, the caption is
simply read back; if `caption` runs first, it triggers that same idempotent call.
Either way there is exactly one API round-trip per photo — this stage's job is to
supply the grounding context and to store/format the result.

**Context passed to the agent is whatever the album has**, so captions are as
grounded as the metadata allows:

- Segment name and place, if the album has segments → *"Late afternoon at the
  harbour in Lisbon"*
- `Album.meta` extras (couple's names, event type) if present
- Nothing at all → still produces an accurate, if generic, caption

This is the payoff of keeping [`Segment`](/foundations/domain-model.md) structured rather than
opaque: the caption prompt can meaningfully interpolate it.

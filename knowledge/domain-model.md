---
type: Domain Model
title: Album and Segment
description: The central abstraction — a bounded set of photos with optional, structurally-typed periods.
tags: [domain, album, segment, abstraction]
timestamp: 2026-07-12
---

# Album and Segment

This is the central abstraction, so it's worth stating the reasoning.

An **Album** is any bounded set of photos the user wants bound into a book.
That's the only universal concept.

A **Segment** is an *optional* named period of time within an album, with an
optional place. This is the generalization of what a travel itinerary does — and
the same shape covers every other album type:

| Album type | What a Segment is |
|---|---|
| Trip | A day (or leg) of the itinerary — "Day 4, Lisbon" |
| Wedding | A phase of the day — "Ceremony, St. Mary's", "Reception, The Barn" |
| Baby's first year | A month or milestone — "Month 3", "First steps, home" |
| Festival | A day, or a stage/act |
| Year of family photos | A month, or an event — "Christmas, grandma's" |

The pipeline reasons about segments *structurally* — they're ordered,
time-bounded, and optionally placed — without knowing or caring what they mean
semantically. That's what keeps [`cluster`](/stages/cluster.md) and
[`caption`](/stages/caption.md) able to do something useful with them while
staying generic.

**Segments are optional.** An album with no segments still works:
[`cluster`](/stages/cluster.md) falls back to pure time-gap scene detection, and
[`caption`](/stages/caption.md) produces ungrounded (but still accurate)
captions. Segments make the output *better*, not possible.

## Design note — why not just a free-form `metadata: dict` on Photo?

Because then no stage could reason about it. The value of the itinerary was never
"arbitrary key-value data" — it was "this time range has a name and a place,"
which is precisely what lets `cluster` assign photos to a narrative unit and
`caption` say "harbour in Lisbon" instead of "a scenic view." Flattening that to
an opaque dict would make the pipeline dumber, not more general. A
structured-but-optional `Segment` is the right level of abstraction: universal in
shape, flexible in meaning.

A free-form `Album.meta` JSON column is still there as an escape hatch for
genuinely album-specific extras (e.g. the couple's names, a trip's country) that
stages don't need to reason about but templates and caption prompts can
interpolate. See [Data model](/data-model.md).

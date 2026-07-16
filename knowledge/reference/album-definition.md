---
type: Configuration Format
title: Album definition on disk (album.yml)
description: The single YAML file that defines an album — slug, title, timezone, meta, and optional segments.
tags: [configuration, yaml, album, segment]
timestamp: 2026-07-12
---

# Album definition on disk

Each album is defined by one YAML file — no code, no DB fiddling.
[`definition.py`](/build/build-order.md) loads it into [`Album` + `Segment`
rows](/reference/data-model.md), normalizing all segment times to UTC.

# Examples

A trip-shaped album:

```yaml
# data/albums/honeymoon-2026/album.yml
slug: honeymoon-2026
title: Our Honeymoon
immich_album_id: 8f2c...
timezone: Europe/Podgorica   # default zone for any time below lacking an offset
meta:
  occasion: honeymoon
  names: [Alex, Sam]

segments:              # optional — omit entirely for an unstructured album
  - label: "Day 1"
    place: "Dubrovnik"
    start_at: 2026-05-02T00:00:00+02:00   # explicit offset; overrides `timezone`
    end_at:   2026-05-02T23:59:59+02:00
  - label: "Day 2"
    place: "Lisbon"
    start_at: 2026-05-03T00:00:00+02:00
    end_at:   2026-05-03T23:59:59+02:00
```

A wedding album is the same file with different segments:

```yaml
slug: smith-wedding
title: Anna & Tom
timezone: Europe/London
segments:
  - label: "Getting ready"
    place: "The Old Rectory"
    start_at: 2026-08-15T09:00:00+01:00
    end_at:   2026-08-15T13:00:00+01:00
  - label: "Ceremony"
    place: "St. Mary's"
    start_at: 2026-08-15T13:00:00+01:00
    end_at:   2026-08-15T14:30:00+01:00
```

**Adding an album requires: this YAML file + an Immich album. Nothing else.**

## Lead faces (optional priority subjects)

An optional `lead_faces` block names the key people — the couple, the baby, the
guest of honour — so [`select`](/stages/select.md) prioritizes photos of them.
Set **exactly one** of three inputs (see [Immich enrichment](/build/enrichment.md)):

```yaml
lead_faces:
  reference_image: leads.jpg          # PRIMARY — a local photo of the leads,
                                      # resolved next to album.yml, uploaded to
                                      # Immich, then its detected faces name them.
  # reference_asset_id: 3fa85f64-...  # or an asset already in Immich (no upload)
  # person_ids: [3fa85f64-..., ...]   # or the Immich people, named directly
```

We never run our own face model: Immich already recognizes library faces into
`Person`s; the reference is just a way to *name* which people are the leads.
Resolution (upload + waiting out Immich's async face detection, then reading the
faces) happens in [`flow/stages/lead_faces.py`](/stages/index.md), which marks the
matching `Person` rows `is_lead=True`. Omit the block entirely for an album with
no priority subjects — every stage then behaves exactly as before. A reference
face that Immich hasn't assigned to a named person is surfaced as a warning (the
person must be confirmed once in Immich's UI), not silently dropped.

## Timezone convention

- Segment `start_at` / `end_at` should carry an **explicit UTC offset** in the
  ISO timestamp. That offset wins.
- If a time has no offset, the album-level `timezone` (an IANA zone name) is used
  to interpret it.
- A time with neither an offset nor an album `timezone` is an error at load time.
- Everything is normalized to UTC before it reaches the database — see
  [Data model](/reference/data-model.md) and [Open questions](/build/open-questions.md) #3.

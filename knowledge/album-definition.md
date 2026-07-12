---
type: Configuration Format
title: Album definition on disk (album.yml)
description: The single YAML file that defines an album — slug, title, timezone, meta, and optional segments.
tags: [configuration, yaml, album, segment]
timestamp: 2026-07-12
---

# Album definition on disk

Each album is defined by one YAML file — no code, no DB fiddling.
[`definition.py`](/build-order.md) loads it into [`Album` + `Segment`
rows](/data-model.md), normalizing all segment times to UTC.

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

## Timezone convention

- Segment `start_at` / `end_at` should carry an **explicit UTC offset** in the
  ISO timestamp. That offset wins.
- If a time has no offset, the album-level `timezone` (an IANA zone name) is used
  to interpret it.
- A time with neither an offset nor an album `timezone` is an error at load time.
- Everything is normalized to UTC before it reaches the database — see
  [Data model](/data-model.md) and [Open questions](/open-questions.md) #3.

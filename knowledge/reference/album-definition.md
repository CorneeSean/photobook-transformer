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

**`segments` have three authoring *sources*, one on-disk shape.** You can write them
by hand (above), extract them from a `--source-document` PDF, or derive them from
subfolders via a manifest (`generate <root> --manifest` — each subfolder's window is
`[min, max]` of its photos' capture timestamps; see
[folder-structure-driven ingestion](/build/archive/folder-structure-ingestion.md)). All three
produce the identical `segments` block this file documents, validated by the same
loader — the source differs, the shape does not.

## Lead faces (optional priority subjects)

An optional `lead_faces` block names the key people — the couple, the baby, the
guest of honour — so [`select`](/stages/select.md) prioritizes photos of them.
Set **exactly one** of three inputs (see [Immich enrichment](/build/archive/enrichment.md)).
Every input is a *list*, so naming one lead is just the one-element case:

```yaml
lead_faces:
  reference_images: [alex.jpg, sam.jpg]  # PRIMARY — local photos of the leads
                                          # (one or more), resolved next to
                                          # album.yml, uploaded to Immich; their
                                          # detected faces are unioned to name them.
  # reference_asset_ids: [3fa85f64-...]   # or assets already in Immich (no upload)
  # person_ids: [3fa85f64-..., ...]       # or the Immich people, named directly
```

Instead of hand-authoring this block, pass `--lead-face IMAGE` to `generate`/`run`
(repeat it per person, e.g. `--lead-face alex.jpg --lead-face sam.jpg`) — the CLI
writes the `reference_images` block for you (absolute paths). On `run` and
`generate --force` it patches the block onto the existing `album.yml`.

We never run our own face model — Immich already recognizes library faces into
`Person`s; the references just *name* which are leads.
[`flow/stages/lead_faces.py`](/stages/index.md) resolves them (upload → wait out async
detection → read faces) and marks matching `Person` rows `is_lead=True`. Omit the block
for an album with no priority subjects (every stage then behaves as before). A reference
face Immich hasn't assigned to a named person surfaces a warning (confirm the person
once in Immich's UI), not a silent drop.

## Timezone convention

- Segment `start_at` / `end_at` should carry an **explicit UTC offset** in the
  ISO timestamp. That offset wins.
- If a time has no offset, the album-level `timezone` (an IANA zone name) is used
  to interpret it.
- A time with neither an offset nor an album `timezone` is an error at load time.
- Everything is normalized to UTC before it reaches the database — see
  [Data model](/reference/data-model.md) and [Open questions](/build/open-questions.md) #3.

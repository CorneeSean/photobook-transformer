---
type: Build Playbook
title: "Step 32: Ingest manifest schema + loader (ingest.yml → validated albums/segments)"
description: Plan for a new pure src/album/manifest.py — Pydantic models (Manifest/AlbumSpec/SegmentSpec) + load_manifest() that parses a root ingest.yml into validated album- and segment-boundary declarations, with all shape/relationship validation and no filesystem or Immich I/O.
tags: [build, ingest, manifest, schema, validation, plan]
timestamp: 2026-07-18
---

# Step 32: Ingest manifest schema + loader

**Status: todo.** First step of the
[folder-structure-driven ingestion playbook](folder-structure-ingestion.md).
Foundational and standalone — no dependency on steps 33/34; they depend on it.

## Why

The manifest is the single new source of truth for folder-driven ingestion
([decision 1](folder-structure-ingestion.md#decisions-settled-with-the-user-before-planning)).
This step delivers just its **schema + loader** — pure, no side effects — so the
mechanism is fully unit-testable in isolation before any upload/fan-out wiring lands.
It mirrors `definition.py`'s posture: parse + validate a YAML file into validated
models, and let filesystem/Immich concerns live in the caller (steps 33/34).

## New module — `src/album/manifest.py`

Three Pydantic models plus a `load_manifest(path) -> Manifest`. Genericity is
load-bearing: the module, fields, and docstrings speak only "album / segment / path"
— never trip/day/occasion (same rule as `agents/segments.py`).

```python
class SegmentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")   # typo safety (unlike AlbumYAML's meta-dict)
    path: str                        # relative to the album path; folder → one Segment
    label: str | None = None         # default at ingest time = titleize(folder name)
    place: str | None = None         # optional; no geocoding here

class AlbumSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str                        # relative to the manifest's directory (the root)
    slug: str | None = None          # default at ingest time = slugify(folder name)
    title: str | None = None         # default at ingest time = titleize(folder name)
    timezone: str | None = None      # else the CLI --timezone default
    source_document: str | None = None   # relative to the album path; mutually exclusive with segments
    lead_faces: list[str] | None = None  # local photos, resolved under the album path (step 34 writes them)
    segments: list[SegmentSpec] = Field(default_factory=list)

class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    albums: list[AlbumSpec] = Field(min_length=1)
```

`SegmentSpec` is deliberately **not** `SegmentYAML`: the manifest declares *which
folder* is a segment (`path` + optional label/place); the `start_at`/`end_at` window
is derived from the photos in step 33. Keeping them separate keeps this module free of
timestamp logic.

### Validation (all pure — no disk access)

A `@model_validator(mode="after")` on `Manifest` enforces the cross-item rules; keep
each rule small and separately asserted (xenon/complexity):

- **Album `path`s** are non-empty, **relative**, and do not escape the root
  (no leading `/`, no `..` component after normalization). Reject duplicates.
- **Resolved slugs are unique.** Compute the effective slug for each album
  (`slug or _slugify(Path(path).name)` — reuse `ingest._slugify`, or hoist a shared
  helper) and reject a collision, since two albums can't share `data/albums/<slug>/`.
- **Segment `path`s** are relative and, once joined to the album path, stay **under**
  the album path (same no-escape rule).
- **Segment paths within an album are mutually non-nesting** — reject when one is a
  path-prefix of another (`Path`-component prefix, not string prefix, so `day-1` is not
  treated as a prefix of `day-10`). This guarantees each photo maps to at most one
  segment; overlapping *time* windows are caught later by the `AlbumYAML` loader.
- **`segments` and `source_document` are mutually exclusive** per album (one segment
  source; [playbook detail](folder-structure-ingestion.md#folder-derived-segment-windows--the-load-bearing-detail)).

**Deliberately not validated here:** on-disk existence of any path and whether a folder
contains images. Those are filesystem I/O and belong to the ingest layer (steps 33/34),
keeping this loader pure and trivially unit-testable — the same split as
`AlbumDefinition` (shape/relationship validation) vs `ingest._collect_images`
(filesystem). State this in the module docstring.

```python
def load_manifest(path: Path) -> Manifest:
    """Read + validate a root ingest.yml into a Manifest. Pure shape/relationship
    validation only — path existence and image presence are checked at ingest time.
    Raises ValueError (pydantic) on a malformed manifest so the CLI can surface it
    before any upload."""
    raw = yaml.safe_load(path.read_text())
    return Manifest.model_validate(raw)
```

`ValueError`/`pydantic.ValidationError` is the failure channel (a `ValidationError`
*is* a `ValueError` subclass), matching how `AlbumYAML` failures surface and how
`cli.py` already catches `ValueError` for a clean one-line message.

## Tests (TDD — write first, per [working-agreement.md](../../process/working-agreement.md))

New `tests/album/test_manifest.py`, `class TestManifest:` (mirrors `test_definition.py`):

- A minimal one-album manifest loads; defaults left `None` stay `None` (deferred to
  ingest-time resolution).
- A rich manifest (slug/title/timezone override, nested segment paths, per-album
  lead_faces) round-trips into the expected model.
- **Rejections** (each its own test): empty `albums`; absolute or `..`-escaping album
  path; duplicate album path; colliding resolved slugs (one explicit `slug` equal to
  another's defaulted slug); segment path escaping its album; **nested** segment paths
  within an album; both `segments` and `source_document` set; an unknown top-level or
  per-album key (`extra="forbid"`).
- `day-1` vs `day-10` are **not** treated as a nesting collision (component-prefix, not
  string-prefix — a regression guard for the nesting check).

Pure/DB-free/torch-free, so it runs in the default `poe check` collection.

## Docs to update

- [folder-structure.md](../../reference/folder-structure.md) — add the `manifest.py` row to
  the `src/album/` tree ("parses a root `ingest.yml` into album/segment declarations").
- [progress.md](../progress.md) — move the step-32 row Outstanding → Completed on merge.
- [log.md](../../log.md) — record the change once implemented.

## Verification (when implemented)

`uv run poe check` (ruff, format, basedpyright, xenon, pytest) green — this step is
pure Python, so the gate fully covers it (no live-service gap).

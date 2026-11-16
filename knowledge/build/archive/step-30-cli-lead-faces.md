---
type: Build Playbook
title: "Step 30: CLI flag for multiple lead-face reference photos"
description: Plan for a repeatable --lead-face flag on generate/run so priority subjects can be named without hand-authoring album.yml.
tags: [build, cli, lead-faces, plan]
timestamp: 2026-07-18
---

# Step 30: CLI flag for multiple lead-face reference photos

**Status: done.** See [progress.md](../progress.md).

> **As-built (2026-07-18) — diverged from this plan on two points.** The sections
> below are the original plan; the shipped design differs as follows.
>
> 1. **Three symmetric list inputs, no singular forms.** Verifying the plan
>    surfaced that dropping the scalar column orphaned the singular `reference_image`
>    path and three call sites. Rather than keep a scalar input plus a new list
>    input, the single-item forms were removed entirely so single is a strict subset
>    of multiple. Shipped schema: `reference_images`, `reference_asset_ids`,
>    `person_ids` (all lists; one lead = the one-element case).
> 2. **No DB upload cache — rely on Immich content-dedup.** Adversarial review
>    caught that the plan's *count-keyed* cache silently kept the old lead when a
>    reference photo was swapped for another of the same count (breaking the very
>    `run`/`generate --force --lead-face` replace path the flag exists for). The
>    hand-rolled cache was reimplementing — incorrectly — the content-dedup Immich
>    already does. So the resolver now **re-uploads the reference photos every run**
>    and lets Immich dedup them (identical bytes → the existing id, a changed photo
>    → a new one → the swap takes effect). The `lead_reference_asset_id` cache
>    column is therefore **dropped**, not generalized: migration `a7e3f9c21b84`
>    (`down_revision` `88a302b1efdf`) only drops it. See `flow/stages/lead_faces.py`.

## Why

Naming an album's priority subjects (`lead_faces`) today requires
hand-authoring `album.yml`, and its `reference_image` input supports exactly
**one** local photo (see [album-definition.md](../../reference/album-definition.md),
[enrichment.md](enrichment.md)). This adds a repeatable `--lead-face
IMAGE` CLI flag so one or more reference photos can be passed directly, with
`generate`/`run` doing the `album.yml` bookkeeping — matching how `generate`
already auto-materializes `album.yml` for `slug`/`title`/`segments`.

Decisions made (with the user) before writing this plan:
- **Cache like the existing single-image path** — generalize the DB cache
  column to a list (needs a migration) rather than relying purely on Immich's
  own content-based upload dedupe.
- **Both `generate` and `run`** get the flag — `run --lead-face ... --force`
  lets someone patch leads onto an already-ingested album without
  hand-editing YAML, not just at first ingest.

## Schema — `src/album/definition.py`

Add `reference_images: list[str] | None = None` to `LeadFacesYAML` (currently
lines 32-49) as a fourth mutually-exclusive input, alongside the existing
`reference_image` / `reference_asset_id` / `person_ids`. Update
`_provided_fields`/`_exactly_one_input` and the class docstring to describe
four inputs instead of three.

## DB — `src/album/db/models.py` + migration

Generalize the existing single-asset cache to a list, following the
`Photo.person_ids` column's established `JSON` pattern:

```python
# was: lead_reference_asset_id: Mapped[str | None] = mapped_column(default=None)
lead_reference_asset_ids: Mapped[list[str] | None] = mapped_column(JSON, default=None)
```

Migration via `uv run poe makemigration -m "generalize album lead_reference_asset_id to a list"`,
then hand-edit to match the existing style (docstring + rationale, `sa.JSON()`):

```python
def upgrade() -> None:
    op.add_column("albums", sa.Column("lead_reference_asset_ids", sa.JSON(), nullable=True))
    op.drop_column("albums", "lead_reference_asset_id")

def downgrade() -> None:
    op.add_column("albums", sa.Column("lead_reference_asset_id", sa.String(), nullable=True))
    op.drop_column("albums", "lead_reference_asset_ids")
```

No backfill needed — it's a cache; losing it just costs one extra (Immich-deduped) upload on the next run for any album mid-flight.

## Resolver — `src/album/flow/stages/lead_faces.py`

**Unify the two local-photo paths.** Because the migration drops the scalar
`lead_reference_asset_id`, the existing `reference_image` branch and its
`_reference_asset_id` method (currently lines 166-199) can no longer read that
column. `reference_image` is just the one-photo case of `reference_images`, so
collapse both into a single list-backed branch and **delete `_reference_asset_id`
(singular)**. In `_resolve_person_ids` (currently lines 154-173), replace the
`reference_image` branch with:

```python
# reference_image / reference_images: upload (once) then wait out Immich's
# async detection. reference_image is just the one-photo case.
images = config.reference_images if config.reference_images is not None else [config.reference_image]
asset_ids = self._reference_asset_ids(album_id, images, reference_dir, session, result)
faces = [face for asset_id in asset_ids for face in self._await_faces(asset_id)]
return self._person_ids_from_faces(faces, result)
```

New `_reference_asset_ids` replaces `_reference_asset_id`, cache-checked by
**count** (a coarse invalidation — same philosophy as today's single-image
cache, which never invalidates on content change either, only ever populated
once). The field label in the error follows which input the user set:

```python
def _reference_asset_ids(self, album_id, images, reference_dir, session, result):
    album = session.get(Album, album_id)
    if album is None:
        raise ValueError(f"no album with id {album_id}")
    if album.lead_reference_asset_ids is not None and len(album.lead_reference_asset_ids) == len(images):
        return album.lead_reference_asset_ids  # cached from a previous run — don't re-upload

    asset_ids = []
    for image in images:
        path = reference_dir / image
        if not path.is_file():
            raise LeadFaceResolutionError(
                f"lead_faces reference image {image!r} not found at {path}",
            )
        asset_ids.append(self._provider.upload_asset(path))
    album.lead_reference_asset_ids = asset_ids
    session.flush()
    result.uploaded_reference = True
    return asset_ids
```

`_person_ids_from_faces` already de-dupes via `dict.fromkeys`, so unioning
faces across multiple reference photos is a straight concat before that call
— no change needed there. Update the module/class docstrings ("the three
inputs" → four) **and the `run` docstring** (line ~122) that names
`Album.lead_reference_asset_id`.

## Ingest / CLI plumbing

**`src/album/ingest.py`** — a small shared helper next to `_materialize`:

```python
def _lead_faces_block(images: list[Path]) -> dict[str, object]:
    """`lead_faces` YAML for CLI-supplied reference photos. Absolute paths so
    they resolve correctly regardless of `reference_dir` (pathlib's `/`
    discards the left operand when the right is absolute)."""
    return {"reference_images": [str(path.resolve()) for path in images]}


def patch_lead_faces(album_yml: Path, images: list[Path]) -> None:
    """Overwrite an existing album.yml's `lead_faces` block in place — used by
    `run --lead-face` and `generate --force --lead-face` to add/replace leads
    without hand-editing YAML."""
    if not album_yml.is_file():
        raise IngestError(f"no album.yml at {album_yml} — run `generate` first")
    data = yaml.safe_load(album_yml.read_text()) or {}
    data["lead_faces"] = _lead_faces_block(images)
    album_yml.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
```

`DirectoryIngestor.ingest`/`_materialize` gain a `lead_face_images: list[Path] | None = None`
parameter, threaded through to `_materialize`, which adds
`data["lead_faces"] = _lead_faces_block(lead_face_images)` when given (mirrors
the existing `segments`/`timezone` conditional-write pattern).

`_reuse_existing` also takes `lead_face_images` — when `--force` **and**
`--lead-face` are both given, call `patch_lead_faces` before returning the
slug (so `generate --force --lead-face ...` behaves like `run --lead-face
...`, not silently ignoring the flag the way `--timezone`/`--source-document`
already do on the reuse path).

`ingest_directory` gets the same new parameter, passed through to
`DirectoryIngestor(...).ingest(...)`.

**`src/album/cli.py`**:
- New shared arg helper (parallel to `_add_captioner_arg`):
  ```python
  def _add_lead_face_arg(parser: argparse.ArgumentParser) -> None:
      parser.add_argument(
          "--lead-face",
          dest="lead_faces",
          metavar="IMAGE",
          action="append",
          help="A local photo naming a priority subject (repeatable for multiple people)",
      )
  ```
  Called from both `_add_generate_parser` and `_add_run_parser`.
- `_generate`: build `lead_face_images = [Path(p) for p in args.lead_faces] if args.lead_faces else None`
  and pass it into `ingest_directory(...)`.
- `_run`: if `args.lead_faces`, call
  `patch_lead_faces(_ALBUMS_DIR / args.album / "album.yml", [Path(p) for p in args.lead_faces])`
  before `pipeline(...)`; add `IngestError` to its `except` tuple (currently
  only `FileNotFoundError, ValueError`) since `patch_lead_faces` can raise it.

## Docs to update

- [Album definition](../../reference/album-definition.md) — document `reference_images` and the `--lead-face` CLI flag.
- [Immich enrichment](enrichment.md) — "three `album.yml` inputs" → four; mention the CLI shortcut.
- [Data model](../../reference/data-model.md) — update the `Album.lead_reference_asset_id` description to the renamed/generalized column.
- **Model comment** `src/album/db/models.py` (the `lead_reference_asset_id` block, ~lines 54-58) — rename to `lead_reference_asset_ids`, reword "asset id" → "asset ids".
- [Progress tracker](../progress.md) — move this row from Outstanding to Completed once merged.
- [Change log](../../log.md) — record the change once implemented.

## Tests (TDD — write first, per [working-agreement.md](../../process/working-agreement.md))

- `tests/album/test_definition.py` — `reference_images` accepted; still exactly one of the four inputs enforced (reject two set, reject none set).
- `tests/album/flow/stages/test_lead_faces.py` — multi-image upload + face union across images; cache hit when `lead_reference_asset_ids` length matches `reference_images` length (no upload calls); cache miss/re-upload on count mismatch; unassigned faces counted across multiple images; missing file in the list raises `LeadFaceResolutionError` naming the missing entry.
- `tests/album/test_ingest.py` — `_materialize` writes `lead_faces.reference_images` when `lead_face_images` given; `_reuse_existing` with `force=True` and `lead_face_images` patches the existing file's `lead_faces` block via `patch_lead_faces`; `patch_lead_faces` on a missing `album.yml` raises `IngestError`.
- `tests/album/test_cli.py` — `--lead-face` repeatable and threaded into `ingest_directory` for `generate`; `run --lead-face` calls `patch_lead_faces` before `pipeline(...)`.
- A DB-touching test (pgvector/testcontainers tier, or in-memory SQLite if the existing lead-faces tests already use that) covering the migration's column rename round-trips through the ORM. **Note:** the existing `tests/album/db/test_pgvector_integration.py::test_no_model_migration_drift` already asserts the migrated schema matches the ORM, so it will catch a mismatched migration for free.

**Call sites the column rename breaks (must be updated in the same commit):**
- `tests/album/flow/stages/test_lead_faces.py` — `test_reference_image_uploads_then_polls_until_detected` asserts `.lead_reference_asset_id == "ref-1"` → `.lead_reference_asset_ids == ["ref-1"]`; `test_reference_image_reuses_cached_asset_id` builds `make_album(..., lead_reference_asset_id="cached-1")` → `lead_reference_asset_ids=["cached-1"]`.
- `tests/album/immich/test_immich_ml_integration.py` (~line 170) — `reference_asset_id = album.lead_reference_asset_id` → read the first element of `album.lead_reference_asset_ids` (guard `None`/empty).
- `tests/album/db/test_pgvector_integration.py` (~line 77) — the `("albums", "lead_reference_asset_id") in columns` assertion → `("albums", "lead_reference_asset_ids")`.

## Verification (when implemented)

- `uv run poe check` (full gate: ruff, format, basedpyright, xenon, pytest) — must stay green.
- Manual: `uv run poe up && uv run poe migrate`, then
  `uv run photobook-generator generate ./some/photos --lead-face a.jpg --lead-face b.jpg`,
  inspect the written `data/albums/<slug>/album.yml` for the `lead_faces.reference_images`
  block, then confirm `Person.is_lead` gets set for the expected people after
  the pipeline runs. Re-run with `--force` and confirm no re-upload (cache hit).

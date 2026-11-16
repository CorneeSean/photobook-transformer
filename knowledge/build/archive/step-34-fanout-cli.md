---
type: Build Playbook
title: "Step 34: Fan-out orchestration + generate --manifest (one tree → N books)"
description: Plan for ingest_manifest() — one shared ImmichClient ingesting every declared album with per-album failure isolation — and the generate --manifest CLI flag that runs the existing pipeline per successfully-ingested album, rejecting the per-album flags and printing a combined books-written / albums-failed summary.
tags: [build, ingest, manifest, fan-out, cli, isolation, plan]
timestamp: 2026-07-18
---

# Step 34: Fan-out orchestration + `generate --manifest`

**Status: todo.** Depends on [step 32](step-32-manifest-schema.md)
(`load_manifest`) and [step 33](step-33-folder-segments.md) (`ingest_album`).
Both this step and step 33 edit `ingest.py`, so they **cannot** be built in parallel.
Third step of the
[folder-structure-driven ingestion playbook](folder-structure-ingestion.md).

## Why

Steps 32–33 gave a validated manifest and a single-album `ingest_album`. This step
drives the fan-out: read the manifest, ingest **every** declared album under one shared
Immich client with per-album isolation, then run the existing `pipeline` once per
successfully-ingested album — N independent books
([decision 2](folder-structure-ingestion.md#decisions-settled-with-the-user-before-planning)).

## Orchestration — `src/album/ingest.py`

`ingest.py` stays **pipeline-free** (it "talks only to Immich and the filesystem" —
its module docstring; importing `pipeline` would pull Prefect in). So it owns the
*ingest* fan-out only, returning outcomes the CLI turns into pipeline runs:

```python
@dataclass(frozen=True)
class AlbumOutcome:
    spec: AlbumSpec
    slug: str | None            # set when ingest succeeded
    error: str | None           # set when it failed (isolated)

def ingest_manifest(
    root: Path, manifest: Manifest, *,
    timezone: str | None = None, force: bool = False,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,   # threaded for tests
    poll_timeout_s: float = _DEFAULT_POLL_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
    model: Model | None = None,
) -> list[AlbumOutcome]:
    """Ingest every album in `manifest` under ONE shared ImmichClient, isolating
    per-album failures (empty folder, no dated photos, an invalid derived album.yml).
    Returns one AlbumOutcome per album, in manifest order."""
    with ImmichClient.from_settings() as client:
        ingestor = DirectoryIngestor(client, poll_interval_s=..., sleep=..., model=model)
        outcomes = []
        for spec in manifest.albums:
            try:
                slug = ingestor.ingest_album(spec, root, timezone_default=timezone, force=force)
                outcomes.append(AlbumOutcome(spec, slug, None))
            except (IngestError, ValueError, FileNotFoundError) as exc:
                _log.warning("skipping album %r: %s", spec.path, exc)
                outcomes.append(AlbumOutcome(spec, None, str(exc)))
        return outcomes
```

Per-album isolation mirrors the pipeline's per-photo isolation pattern (rule: a bad
unit is logged and skipped, not fatal). The **manifest itself** is validated up front
(step 32) so a malformed manifest fails *before* the client opens — this loop only
isolates per-album ingest failures. `timezone_default` threads the CLI `--timezone`
into each `ingest_album` (a spec's own `timezone` still wins — a small addition to
step 33's signature, noted here as this step's dependency on it).

## CLI — `src/album/cli.py`

Add `--manifest` to the `generate` sub-parser (opt-in per
[decision 3](folder-structure-ingestion.md#decisions-settled-with-the-user-before-planning)):

```python
parser.add_argument(
    "--manifest", nargs="?", const="ingest.yml", metavar="FILE",
    help="Fan a directory tree into N albums from a manifest "
         "(default: <DIR>/ingest.yml). Turns generate into a multi-album run.",
)
```

`_generate` branches on `args.manifest`:

- **Absent** → today's single-album path, byte-for-byte unchanged.
- **Present** → `_generate_manifest(args)`:
  1. **Reject the per-album flags**
     ([decision 4](folder-structure-ingestion.md#decisions-settled-with-the-user-before-planning)):
     if any of `--slug/--title/--source-document/--lead-face` is set, exit(1) with a
     clear message ("… are per-album; set them in the manifest"). `--timezone` (global
     default), `--captioner`, `--force` are allowed.
  2. Resolve `root = Path(args.directory)`, `manifest_path = root / args.manifest`
     (or `args.manifest` if absolute); `manifest = load_manifest(manifest_path)`.
  3. `outcomes = ingest_manifest(root, manifest, timezone=args.timezone, force=args.force)`.
  4. For each `outcome.slug` that ingested, run the pipeline **isolated**:
     ```python
     try:
         pipeline(album_slug=slug, captioner=_resolve_captioner(args.captioner), force=args.force)
         books.append(slug)
     except (FileNotFoundError, ValueError) as exc:   # keep real bugs surfacing
         failures.append((slug, str(exc)))
     ```
  5. Print a **combined summary**: each book path
     (`_ALBUMS_DIR / slug / "book/index.html"`) written, plus every ingest/pipeline
     failure with its reason. Exit non-zero if **every** album failed; exit zero if at
     least one book was produced (partial success is success — decision 6). The summary
     is the user-facing contract for a multi-GB run, so make it explicit and readable.

Keep `cli.py` thin: `_generate_manifest` is arg-validation + two loops + a print; all
ingest logic stays in `ingest.py`, all pipeline logic in `flow`.

## Tests (TDD — write first, per [working-agreement.md](../../process/working-agreement.md))

- `tests/album/test_ingest.py`, `class TestIngestManifest:` (fake `IngestClient`):
  three-album manifest → three `AlbumOutcome`s with slugs; a middle album with an
  empty folder → that one's `error` set, the others still succeed (isolation); manifest
  order preserved.
- `tests/album/test_cli.py`:
  - `--manifest` with `--slug`/`--title`/`--source-document`/`--lead-face` → exit(1)
    with the per-album-flags message (one test each, or parametrized).
  - `--manifest` (no value) resolves to `<DIR>/ingest.yml`; an explicit value is used
    verbatim; a relative value resolves under `<DIR>`.
  - Happy path with `ingest_manifest` + `pipeline` patched: pipeline called once per
    ingested slug; summary lists each book path.
  - One album's `pipeline` raises → its failure is isolated and reported; other books
    still print; exit code reflects partial success (zero) vs total failure (non-zero).
  - Flat `generate <dir>` (no `--manifest`) still calls the single-album path
    unchanged (regression guard).

DB-free/torch-free — default `poe check` collection.

## Docs to update

- [folder-structure-ingestion.md](folder-structure-ingestion.md) — mark the
  fan-out + CLI as built; add any as-built deviations.
- [progress.md](../progress.md) — move the step-34 row Outstanding → Completed on merge.
- [log.md](../../log.md) — record the change once implemented.

(README + `folder-structure.md` + the end-to-end genericity test are
[step 35](step-35-docs-verify.md).)

## Verification (when implemented)

`uv run poe check` green. Live multi-album fan-out against a real Immich is deferred to
the first live run (standing no-reachable-Immich gap); the fake-client + patched-
pipeline tests are the offline stand-in.

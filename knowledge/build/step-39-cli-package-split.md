---
type: Plan
title: "Step 39 follow-up — split cli.py into an album/cli/ package"
description: A pure structural refactor of the single-file src/album/cli.py into an album/cli/ package with responsibilities segregated across parser/sources/runner modules, the _build driver reshaped into a BuildRunner class, and a clean public main entrypoint. No CLI behaviour changes.
tags: [plan, cli, refactor, package, build, step-39, planned]
timestamp: 2026-07-19
status: planned
---

# Step 39 follow-up — split `cli.py` into an `album/cli/` package

## Context

Step 39 unified the old `generate`/`run` subcommands into one strategy-based
`photobook-generator build` command (merged, commits `22acb9c` + `9da4dc6`). The
result works and is `poe check`-green (440/6), but it all lives in a single
~370-line `src/album/cli.py` that mixes four distinct responsibilities: argparse
construction, the `AlbumSource` strategies + selection/validation, and the
`_build`/`_run_pipelines`/`_report` driver, plus shared helpers.

This step turns that file into a **package** `src/album/cli/` with those
responsibilities segregated across modules, reshapes the driver (`_build` and its
directly-related functions) into a **class**, and wires a clean public entrypoint
to that class. This is a pure structural refactor — **no CLI behaviour changes**
(same `build` surface, same exit codes, same output). Logged as a **step 39
follow-up** (not a new step number), per the user's framing.

## Target package layout

Replace `src/album/cli.py` with `src/album/cli/` containing four modules, each a
single responsibility:

| Module | Responsibility | Public names |
|---|---|---|
| `__init__.py` | Public surface + entrypoint; wires parser → runner | `main` |
| `parser.py` | argparse construction only (argv → `Namespace`) | `build_parser()` |
| `sources.py` | The ingest strategies **and** args→strategy selection/validation | `PreparedAlbum`, `AlbumSource`, `DirectoryIngestSource`, `ManifestIngestSource`, `NoIngestSource`, `prepared_from_outcome`, `select_source` |
| `runner.py` | The `BuildRunner` class + the pipeline driver | `BuildRunner`, `run_pipelines` |

Dependency direction (no cycles): `__init__` → `parser`, `runner`; `runner` →
`sources`; `sources` → `album.ingest`/`album.manifest`. `parser` depends only on
`album.ingest.INGEST_MODES` + `album.analysis.captioner.captioner_names`.

## Function → new-home mapping (move verbatim, no logic changes)

- **`parser.py`** — `build_parser()` returns the top-level parser; folds today's
  `main()` parser setup + `_add_build_parser()`. Keep `set_defaults(parser=<build
  subparser>)`; **drop the `func` default** (single command — no dispatch table
  needed).
- **`sources.py`** — `PreparedAlbum`, `AlbumSource` (ABC), the three
  `*IngestSource`/`NoIngestSource` strategies, `_prepared_from_outcome` →
  **rename public** `prepared_from_outcome`. Plus the selection/validation:
  `_select_source` → **`select_source`**, and helpers `_ingest_source`,
  `_no_ingest_source`, `_reject_per_album_flags`, `_resolve_manifest_path`,
  `_lead_face_images` (module-private, stay `_`-prefixed).
- **`runner.py`** — a `BuildRunner` class built from today's `_build`:
  ```python
  class BuildRunner:
      def __init__(self, args: argparse.Namespace, parser: argparse.ArgumentParser) -> None: ...
      def run(self) -> None:            # was _build(...)
      def _report(self, books, failures) -> None: ...   # was _report
  ```
  `run()` resolves the captioner, calls `select_source(...).prepare()` inside the
  existing `(FileNotFoundError, ValueError, IngestError)` guard (exit 1), then
  `run_pipelines(...)` + `self._report(...)`. Keep `run_pipelines(prepared,
  captioner, *, force)` as a **module-level function** (pure `prepared →
  (books, failures)` transform — reused by `test_genericity.py`), plus module
  helpers `_resolve_captioner` and `_book_path`.
- **`__init__.py`**:
  ```python
  from album.cli.parser import build_parser
  from album.cli.runner import BuildRunner

  def main() -> None:
      parser = build_parser()
      args = parser.parse_args()
      BuildRunner(args, args.parser).run()

  __all__ = ["main"]
  ```

Entry point in `pyproject.toml` stays `photobook-generator = "album.cli:main"` —
resolves to `album/cli/__init__.py:main`, **no change needed**.

## Consumers to update

1. **`tests/album/test_cli.py`** — monkeypatch targets move to where each name is
   now looked up (patch-at-use-site):
   - `album.cli.get_settings` → `album.cli.runner.get_settings`
   - `album.cli.pipeline` → `album.cli.runner.pipeline`
   - `album.cli.ingest_directory` / `ingest_manifest` / `load_manifest` /
     `patch_lead_faces` → `album.cli.sources.<name>`
   No test *logic* changes — same argv, same assertions, same exit codes.
2. **`tests/album/test_genericity.py`** — imports move: `from album.cli.sources
   import prepared_from_outcome` + `from album.cli.runner import run_pipelines`;
   the monkeypatch of `album.cli.pipeline` → `album.cli.runner.pipeline`; the call
   `_run_pipelines(...)`/`_prepared_from_outcome(...)` → the un-prefixed names.
3. **Docs** (living only; leave historical log/archive/build-order entries):
   - `knowledge/reference/folder-structure.md` — replace the single `cli.py`
     tree line with a `cli/` package node listing the four modules + one-line roles.
   - `knowledge/foundations/architecture.md` — "typically from `cli.py`" →
     "`cli/` package".

## Bookkeeping (step 39 follow-up)

- `knowledge/build/progress.md` — append a **Follow-up** note to the existing
  step-39 row (its new commit hash + the package split); do **not** add a step 40.
- `knowledge/log.md` — new dated bullet "Step 39 (follow-up) — split `cli.py` into
  an `album/cli/` package".
- Two commits on a `step-39-cli-package` branch (code+tests+docs, then
  tracker/log), ff-merge to `main`, per the standing workflow.

## Verification

1. `uv run poe check` green (ruff + format + basedpyright + xenon + pytest) — the
   440/6 suite must stay green with only the monkeypatch-target/import moves.
2. Package resolves + entrypoint intact:
   `uv run photobook-generator build --help` prints the same usage (positional
   `DIR`, `--no-ingest/--album/--manifest/--ingest-mode/...`).
3. `uv run python -c "from album.cli import main; print(main)"` imports cleanly.
4. Confirm no residual references: `rg -n "album\.cli\.(pipeline|ingest_|load_manifest|patch_lead_faces|get_settings)\b" tests` returns nothing (all retargeted to submodules).
5. Inline review across correctness/reuse/simplification/altitude; keep every block
   xenon rank A (the pieces are already A — this only moves them between files).

## Non-goals

- No change to the `build` CLI surface, flags, exit codes, or output.
- No new functionality; `album.ingest`/`album.flow`/`album.manifest` untouched.
- Historical docs (log/archive/build-order) keep their `cli.py` references.

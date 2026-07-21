# Partial pipeline re-runs: `--from` / `--only` / `--to` + tunable stage params

> **Status: draft.** Not yet a queued build step — a design brief for the
> iterate-on-params workflow. Move into `progress.md`'s Outstanding table when scheduled.

## Context

Today the only way to re-run an album is `photobook-generator build --no-ingest --album <slug>`,
which runs the **whole** DAG. A plain re-run already *skips* completed stages (state lives in
non-NULL DB columns; `NULL` = "not run yet"), but the moment you change a param you must add
`--force` — and `--force` is **global**, so it re-runs everything from `sync` (the expensive Immich
boundary) down. That makes the natural workflow — *upload once, then iterate on params until the
book looks right* — needlessly slow.

**Prefect is not the blocker and does not need to be removed.** In Prefect 3 a flow is just a
Python function and every stage is already an independently-runnable object (`Stage` subclass,
constructed with `album_id` + `session_factory` + `force`, with its own `run()`/`run_as_task()`).
The missing piece is purely a *partial-run entrypoint* plus making the tuning knobs reachable.

**Key correctness fact — downstream invalidation.** Stages read each other's DB columns, so
re-running an early stage leaves downstream state stale. Re-tuning `select` invalidates
`enhance → render → caption → assemble → fulfill`. So the primary control is **"re-run from a
stage onward"** (`--from`), with `--only` as a single-stage escape hatch for debugging and `--to`
as an optional upper bound.

Outcome: `build --no-ingest --album trip --from select` re-runs `select … fulfill` (forced),
**reusing** the already-computed `sync`/`quality`/`cluster` DB state — no re-sync, no re-scoring.

## Canonical stage order

The user-facing stage names are the existing `task_name` ClassVars (so CLI names == Prefect task
names). Order is the critical-path topological order:

```
sync  resolve-leads  quality  cluster  select  enhance  render  caption  assemble  fulfill
 0        1            2        3        4       5        6       7         8         9
```

Selection is a **linear index cut** over this order (simple + safe):
- `--from X`  → run stages with `index >= index(X)`
- `--to X`    → run stages with `index <= index(X)`
- `--only X`  → exactly `X` (== `--from X --to X`); mutually exclusive with `--from`/`--to`
- every **selected** stage runs with `force=True`; unselected stages are **not run** (DB reused).
- default (no flags) = today's behaviour; bare `--force` = select-all + force-all (back-compat).

Caveat to document: a linear cut may re-run an *independent sibling* in a fan-out group
(e.g. `--from quality` also re-runs `cluster`). This is safe (both idempotent) and cheap; use
`--only` when you truly want one stage. Precondition: `--from`/`--only` assume a prior full run
populated the upstream state.

## Implementation

### 1. Stage-selection model — `src/album/flow/` (new small module, e.g. `_selection.py`)
- Define `STAGE_ORDER: tuple[str, ...]` = the ten `task_name`s above (single source of truth;
  add a test asserting it matches each stage class's `task_name`).
- A `StageSelection` value object built from `(from_stage, to_stage, only_stage)`:
  - validates names against `STAGE_ORDER` (raise `ValueError` on unknown → CLI maps to exit 2);
  - rejects `--only` combined with `--from`/`--to`, and `from > to`;
  - exposes `is_selected(stage_name) -> bool`.
  - A sentinel "all selected" instance is the default (whole DAG, force governed by `--force`).

### 2. Wire selection into `Pipeline` — `src/album/flow/_flow.py`
- Add a `selection: StageSelection` constructor kwarg (default = all-selected) alongside `force`.
- In `_execute`, `_run_group_1`, `_run_group_2`, guard each stage:
  - **sequential** stages (`sync`, `select`, `assemble`, `fulfill`): call `.run_as_task()` only if
    `selection.is_selected(name)`; pass `force=selection.is_selected(name) or self._force`.
  - **fan-out** stages: only `.submit()` the selected ones; the `.result()` loop iterates only the
    futures actually submitted (build the future list conditionally).
  - Leave the `db_session.expire_all()` calls in place — they are harmless no-ops when a group is
    skipped, and still correct when a group is partially run.
- Extend `Pipeline.flow()`'s inner `pipeline(...)` signature with the selection inputs
  (`from_stage`/`to_stage`/`only_stage`, or a pre-built `selection`), keeping
  `validate_parameters=False`. Prefer passing the three strings and building `StageSelection`
  inside, so the flow's public shape stays JSON-simple.

### 3. CLI — `src/album/cli/parser.py` + `runner.py`
- `parser.py`: add `--from`, `--to`, `--only` with `choices=STAGE_ORDER` (import the tuple), under
  the "Universal options" block next to `--force`. Update `--force` help to note it now means
  "force the whole pipeline; use --from/--only to force a subset".
- `runner.py`: thread the three values through `BuildRunner.run` → `run_pipelines(...)` →
  `pipeline(album_slug=..., from_stage=..., to_stage=..., only_stage=..., ...)`. Reuse the existing
  `except (FileNotFoundError, ValueError, ...)` path in `run()` so a bad stage name / bad
  combination prints a clean message and exits (matching the captioner-validation precedent).

### 4. Expose key stage params (user-tunable between runs)
These are currently hardcoded constructor defaults. Add `config.py` sub-models (env-driven, per
Rule 6 — `config.py` is the only env reader), each with an `__`-nested group, and wire them where
`Pipeline` constructs the stage:
- **`SelectSettings`** → `SELECT__THRESHOLD`, `SELECT__LEAD_BOOST`, `SELECT__GROUP_BOOST`,
  `SELECT__RATING_BOOST` (defaults = the current `_DEFAULT_*` in `select.py`). Wire into the
  `SelectStage(...)` construction in `_execute`. Note: album-level `similarity_threshold` in
  `album.yml` already overrides the threshold per-album (keep that precedence — album.yml wins).
- **`ClusterSettings`** → `CLUSTER__TIME_GAP_MINUTES`, `CLUSTER__SPATIAL_EPS_M` (map minutes →
  `timedelta`). Wire into `ClusterStage(...)` in `_run_group_1`.
- **Tone (`enhance`)** — *most invasive; flag it.* The tone knobs (`_TARGET_LUMA`, `_SHADOW_GAIN`,
  `_HIGHLIGHT_GAIN`, `_TEMP_CAST_GAIN`, clip points) are **module-level constants** in
  `src/album/analysis/tone_estimator.py`, not constructor params. To expose them: give
  `ClassicalToneEstimator.__init__` params (defaulting to today's constants), add a `ToneSettings`
  sub-model, and build the estimator from it in `Pipeline._build_analyzer` (currently passes
  `tone_estimator=None`). Recommend doing select+cluster first and treating tone as a follow-up in
  the same branch only if time allows — it touches the analysis layer, not just wiring.

Rationale for env/`config.py` (not new CLI flags) for these: keeps the param surface out of
`parser.py`, matches the existing `QUALITY__*` pattern, and each CLI run is a fresh process so env
edits take effect immediately. `album.yml`'s `similarity_threshold` remains the template for
anyone who later wants per-album overrides of the rest.

## Testing (TDD, per AGENTS.md workflow)
- **`_selection.py` unit tests**: name validation, `--only` exclusivity, `from>to` rejection,
  `is_selected` cut boundaries, and `STAGE_ORDER == each stage's task_name`.
- **`tests/album/flow/test_flow.py`**: extend the existing double-injecting tests
  (`test_runs_every_stage_once...`, `test_force_threads...`) with cases:
  - `--from select` runs `select…fulfill` forced, and `sync/quality/cluster` are **not** invoked;
  - `--only caption` runs caption alone;
  - `--to select` stops after select;
  - default run unchanged (regression).
  Use the same collaborator-double pattern already in that file (spy on which stage `run`s).
- **`config.py` tests**: new sub-models parse `SELECT__*` / `CLUSTER__*` env; defaults equal the
  former hardcoded constants.
- **CLI tests** (mirror existing parser/runner tests): flags parse, bad stage name → exit 2,
  `--only` + `--from` → error, values thread into `pipeline(...)`.
- Gate: `uv run poe check` green (ruff + format + basedpyright + xenon + pytest).

## End-to-end verification
1. Full first run: `uv run photobook-generator build ./photos --slug trip` (or `--no-ingest
   --album trip` if already ingested). Confirm a book at `data/albums/trip/book/index.html`.
2. Tune a param (e.g. `SELECT__THRESHOLD=0.85`) and re-run **partial**:
   `uv run photobook-generator build --no-ingest --album trip --from select`.
   Confirm from logs (`<stage>: starting …`) that `sync/quality/cluster` do **not** run and
   `select…fulfill` do, and the book is regenerated. Contrast wall-clock vs a `--force` full run.
3. `--only caption` re-captions without re-rendering (check `render: starting` is absent).
4. Error paths: `--only bogus` and `--only x --from y` exit non-zero with a clean message.

## Docs to update (part of the step, per AGENTS.md)
- `knowledge/foundations/pipeline-execution.md` (add the selection/partial-run section + the
  linear-cut caveat) and `knowledge/build/progress.md` + `knowledge/log.md` (record the step).
- Refresh the `build` help text / any CLI usage in the knowledge bundle.

---
type: Working Agreement
title: Quality practices — code structure & testing
description: How production code is structured (classes, encapsulation, constructor-injected dependencies) and how tests are organized (class-based groups, parametrization, scoped fixtures and helpers).
tags: [process, oop, dependency-injection, testing, pytest, conventions]
timestamp: 2026-07-14
---

# Quality practices — code structure & testing

This document is the project's standard for *how code is shaped*, on top of the
[working agreement](/process/working-agreement.md)'s *when-is-it-done* gate (TDD, typing,
`uv run poe check`) and the [folder structure](/reference/folder-structure.md)'s
*where-does-it-live* rules. Where those documents and this one overlap, this one
is the more specific authority on structure and test organization, and they have
been amended to point here.

Two goals, in tension only occasionally: **encapsulate behaviour behind small
public surfaces**, and **keep every unit trivially testable in isolation**. The
patterns below are the ones that serve both.

## 1. Production code structure

### 1.1 Classes vs. module functions — the hybrid rule

Lean towards **classes**. A unit of behaviour becomes a class when *either* of
these holds:

- it has **real collaborators** — an external boundary it talks to (an Immich
  client, a similarity search, a printer, a vision model, a renderer), or
- it carries **more than one private helper** or a piece of configuration that
  several of its steps share.

A unit stays a **plain module function** only when it is genuinely trivial: one
public entry point, no injected collaborator, at most a helper or two, and no
configuration to hold. Pure, stateless value transforms (`compute_blur_score`,
`spine_width_in`, `reconcile_tone`) stay module-level functions on purpose —
wrapping a pure function in a class adds ceremony without encapsulating anything.

> Applied to the pipeline as it stands, **every one of the nine stages qualifies
> as a class** — each either injects a collaborator or owns several helpers. That
> is expected, not an over-reach: the hybrid rule's "function" branch is for the
> genuinely trivial stage we don't have yet, and for the pure helpers that live
> *inside* a stage module.

### 1.2 The stage shape

A stage class separates its **stable dependencies** (injected once, at
construction) from its **per-run inputs** (passed to the call):

```python
class SelectStage:
    """`select` — diversity-aware best-of-scene via MMR."""

    def __init__(
        self,
        similarity: SimilaritySearch,
        *,
        threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self._similarity = similarity
        self._threshold = threshold

    def run(self, album_id: int, session: Session) -> SelectResult:
        ...

    def _select_scene(self, candidates: list[Photo], session: Session) -> int:
        ...
```

The load-bearing conventions, unchanged from before and now enforced through the
class shape:

- **`run` takes `album_id` first, then the `Session`.** The `album_id`-first
  rule (no stage assumes it owns the database) and the session-per-call rule (the
  `Session` is request-scoped — one shared session per pipeline run, owned by
  `flow.py`, *not* a construction-time dependency of a stage) both survive intact.
  Never inject a `Session` into a constructor.
- **Stable collaborators go in `__init__`** — the Immich client, the similarity
  search, the renderer, the printer, the vision model, tunable thresholds and
  data roots. These are the things a caller (or a test) swaps.
- **Private helpers become private methods** (`_`-prefixed). What was a
  module-level `_select_scene(...)` taking `similarity, threshold` as arguments
  becomes `self._select_scene(...)` reading `self._similarity`, `self._threshold`.
- **Per-run knobs (`force=...`) stay keyword-only arguments on `run`**, not
  constructor state — they vary per invocation, not per wiring.
- **The result is a `@dataclass`** (`SelectResult`, `SyncResult`, …), returned
  from `run`. Frozen when it's a pure value object with no post-construction
  mutation (`BookPage`, `ToneParams`); plain when the stage accumulates counts
  into it during the run (`QualityResult`).

### 1.3 Encapsulation

- **Public surface is the smallest thing a collaborator needs.** For a stage
  that's `run` plus, where relevant, a construction-time factory. Everything else
  is `_`-prefixed and may be reshaped freely — tests target `run`, not the
  private methods (see §2.4).
- **`_`-prefix every internal method, attribute, and module constant.** Stored
  dependencies are `self._similarity`, not `self.similarity` — a stage's injected
  collaborators are implementation detail, not public API.
- **Prefer a factory classmethod for "the real thing".** `ImmichClient.from_settings()`
  is the pattern: the constructor takes explicit dependencies (so tests pass
  fakes), and a `from_settings()` / `from_config()` classmethod builds the
  production instance from `config.py`. Keep the wiring-from-environment in one
  place, out of the constructor.

### 1.4 Dependency injection

- **Inject external boundaries; never reach for them.** A stage that needs
  thumbnails takes a `ThumbnailSource`, it does not import `ImmichClient` and
  construct one. This is already the norm — keep it.
- **Depend on a `Protocol`, not a concrete class**, for anything a test needs to
  substitute. `SimilaritySearch`, `Printer`, `AssetSource`, `ThumbnailSource`,
  `Renderer` are all `Protocol`s; the real class satisfies them structurally and
  a stub in a test satisfies them just as well, with no inheritance. Mark a
  Protocol `@runtime_checkable` **only** when `flow.py` names it in the `@flow`
  signature (Prefect's schema build does an `isinstance` check) — that's the one
  reason this codebase needs it, and it's documented at each such Protocol.
- **Value objects are dataclasses**, passed by value: `ToneParams`, `PhotoContext`,
  `BookPage`, `PrintResult`. Frozen by default; mutable only with a reason.
- **A shared collaborator used by two stages is its own injected object**, not a
  function one stage imports from another. The current `enhance`/`caption` pair
  shares `ensure_analysis` (the "one combined vision call" design — see
  [architecture](/foundations/architecture.md)); under this standard that shared trigger
  becomes a small injected collaborator (e.g. a `PhotoAnalyzer` holding the
  thumbnail source + vision model, with an `ensure(session, photo, *, force)`
  method) that **both** `EnhanceStage` and `CaptionStage` receive in their
  constructors. The "exactly one vision round-trip per photo" contract is
  preserved — it now lives in one owned object instead of a cross-module function
  import.

### 1.5 Wiring in `flow.py`

`flow.py` remains the only Prefect-aware module. It instantiates each stage class
with its real (or injected-for-test) collaborators, then wraps the bound `.run`
in a `@task`:

```python
@task(name="select")
def _select_task(stage: SelectStage, album_id: int, session: Session) -> None:
    stage.run(album_id, session)
    session.commit()
```

`pipeline()` keeps its existing shape — every dependency is a keyword argument
defaulting to the real thing built from `config.py` — but now defaults to a
constructed stage (or the collaborator the stage needs), so `pipeline()` itself
stays unit-testable for wiring/ordering without touching Postgres, Immich,
Claude, darktable-cli, or Lulu.

## 2. Testing conventions

### 2.1 Class-based test groups

Group a module's tests into **test classes**, one per cohesive behaviour area,
rather than a flat list of `test_*` functions:

```python
class TestSelect:
    """MMR selection over in-memory SQLite."""

    def test_highest_aesthetic_score_wins_first(self, session, make_album, make_photo):
        ...

    def test_near_duplicates_skipped(self, session, make_album, make_photo):
        ...


class TestSelectFallbacks:
    """The nullable-embedding and unclustered-photo paths (genericity guards)."""
    ...
```

The class is the scoping unit: helpers and fixtures a group needs live *on or
near the class*, visible only to it (§2.3–2.4). Split into multiple classes when
a file covers distinct behaviours (happy path vs. fallbacks vs. idempotency) —
it keeps each group's fixtures and helpers small and local.

### 2.2 Parametrize within the class

Prefer one parametrized test over several near-identical ones. Table-style cases
belong in `@pytest.mark.parametrize` with `ids=` so failures name the case:

```python
class TestResolveZone:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Europe/Warsaw", ZoneInfo("Europe/Warsaw")),
            ("UTC+02:00", None),   # non-IANA — Immich sometimes emits these
            (None, None),
        ],
        ids=["iana", "non-iana-offset", "missing"],
    )
    def test_resolve_zone(self, raw, expected):
        ...
```

Don't parametrize across genuinely different behaviours just to save lines — one
assertion shape per parametrized test.

### 2.3 Fixtures: shared as factory fixtures, scoped tightly

- **Shared object builders are factory fixtures in `conftest.py`.** The
  `make_album` / `make_segment` / `make_photo` builders become fixtures that
  *return a builder callable*, so a test asks for exactly the builders it uses
  and gets consistent defaults + DI (e.g. a builder that needs the `session`
  depends on it):

  ```python
  @pytest.fixture
  def make_photo() -> Callable[..., Photo]:
      def _make(album: Album, **overrides: object) -> Photo:
          defaults: dict[str, object] = {
              "album": album,
              "immich_asset_id": "asset-1",
              "filename": "IMG_0001.jpg",
          }
          defaults.update(overrides)
          return Photo(**defaults)  # pyright: ignore[reportArgumentType]
      return _make
  ```

- **Scope a fixture to the narrowest place that needs it.** A fixture used across
  the suite lives in the top-level `conftest.py`; one used by a single test file
  lives in that file; one used by a single test class is defined *inside the
  class* (or as a class-scoped fixture) so it isn't visible to unrelated tests.
  Reach for `scope="module"`/`"class"` only for genuinely expensive, read-only
  setup (a Postgres testcontainer) — pure-logic fixtures stay function-scoped so
  no state leaks between tests.

### 2.4 Test doubles: fakes over mocks, scoped to their consumer

- **Substitute a `Protocol` with a small hand-written fake/stub**, not
  `unittest.mock`. `StubSimilarity` (real cosine similarity in numpy) is the
  model: it implements the Protocol, behaves deterministically, and reads like
  the thing it stands in for. Mocks that assert call sequences couple tests to
  internals — avoid them unless the interaction *is* the behaviour under test.
- **Define a double where it's used, scoped to its consumer.** A stub only one
  test class needs is a nested class or a fixture on that class, not a
  module-level name every test in the file sees. A stub shared across files goes
  in `conftest.py` (or a `tests/doubles.py` helper module) — but only once a
  second file actually needs it.
- **Test through the public surface (`run`), never the private methods.**
  Private helpers (`_select_scene`, `_scene_indices`) are covered *via* `run`'s
  observable behaviour and results, so they can be reshaped without rewriting
  tests. If a private helper feels like it needs its own test, that's a signal it
  wants to be its own small public unit (a module function or a collaborator).

### 2.5 Per-file helpers become class methods

A `_`-prefixed module-level helper used only within one test file (`_photo`,
`_by_asset`, `_persist` in `test_select.py`) moves onto the test class that uses
it as a private method (or becomes a fixture when it needs other fixtures). This
keeps the helper's visibility matched to its single consumer and lets it read
`self`-scoped fixtures directly.

### 2.6 What doesn't change

The [working agreement](/process/working-agreement.md)'s test-*strategy* table stands: in-memory
SQLite for pure-logic stages, a Postgres testcontainer for `pgvector`, recorded
HTTP for the Immich client, PydanticAI `TestModel`/`FunctionModel` for agents,
committed fixture images for image ops, manual verification for
darktable/Docker/Lulu. This document governs *organization* (classes,
parametrization, fixture scope), not *what layer* a given stage is tested at.

## 2.7 Enforcement — the complexity gate

Structure discipline is backed by a machine gate, not just review, so a
too-large method fails `uv run poe check` the same way a type error does:

- **ruff mccabe (`C90`)** — a cheap, dependency-free per-function complexity smell
  check (ceiling 10), running inside the existing ruff pass.
- **radon + xenon** — `radon` computes the metrics (cyclomatic complexity,
  maintainability index; `uv run poe metrics` prints them); **xenon** enforces
  them as a gate (`uv run poe complexity`, and inside `poe check` + pre-commit).
  Thresholds: **no block worse than B** (CC ≤ 10), **no module averaging worse
  than B**, **whole-codebase average A**. These three settings are kept in
  lock-step across `poe check`, the `complexity` task, and the pre-commit hook.

The gate was tightened from an initial `--max-absolute C` to **B** once the
stage-class refactor split the two former grade-C blocks (`assemble` and
`enhance._enhance_scene`) into smaller methods. A new unit that can't meet the
gate is the signal it's doing too much — extract a collaborator or a helper
method rather than raising the threshold.

## 3. Applying this standard

- **New and changed code follows it directly** — a new stage is a class from the
  first failing test; new tests are class-grouped with scoped fixtures.
- **The existing code is being retrofitted to match** (the nine stages → stage
  classes, `flow.py` wiring updated, the test suite → class-based groups + factory
  fixtures), landed as its own reviewed change, `uv run poe check` green, no
  behavioural change. The shared-analysis collaborator extraction (§1.4) is the
  one piece with real design content; everything else is mechanical.

## See also

- [Working agreement](/process/working-agreement.md) — the TDD loop and the `uv run poe check` commit gate.
- [Folder structure](/reference/folder-structure.md) — where each unit lives and the genericity/structure rules.
- [Architecture](/foundations/architecture.md) — why the collaborators are injected (idempotent stages, one combined vision call, Prefect only in `flow.py`).
- [Implementation workflow](/process/implementation-workflow.md) — the branch → review → squash → merge delivery loop this change goes through.

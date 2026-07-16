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

> Since the Template Method retrofit (§1.6.1) a stage no longer *writes* its own
> `run`: it subclasses `Stage[…Result]` and implements `_process(album, session,
> *, force)`. The base's `run` performs the invariant album lookup + guard, then
> calls `_process`. The conventions above are unchanged — they are now enforced by
> the base signature rather than restated per stage. The `SelectStage` sketch
> above predates the base; read it as "what `_process` contains", with `run`
> inherited.

### 1.3 Encapsulation

- **Public surface is the smallest thing a collaborator needs.** For a stage
  that's `run` plus, where relevant, a construction-time factory. Everything else
  is `_`-prefixed and may be reshaped freely — tests target `run`, not the
  private methods (see §2.4).
- **`_`-prefix every internal method, attribute, and module constant.** Stored
  dependencies are `self._similarity`, not `self.similarity` — a stage's injected
  collaborators are implementation detail, not public API.
- **A private helper that reads no instance state is a `@staticmethod`.** If
  `_verb_noun` never touches `self._x`, mark it `@staticmethod` (as the base's
  `_load_album` does) — it makes the self-independence explicit and is applied
  uniformly across a class's helpers, not sporadically. A helper that *does* read a
  stored dependency or tunable stays an instance method. This is a consistency
  rule, not a correctness one; keep it uniform within and across the stage family.
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

### 1.6 Design patterns — deliberate, not decorative

Patterns are adopted only where a usage analysis shows a *recurring shape* they
name better than ad-hoc code. The verdicts below are the standing decisions for
this codebase; a new pattern is added the same way — evidence first, then the
name.

#### 1.6.1 Template Method — the `Stage` base (adopted)

Every stage's `run` opens with the same invariant envelope: load the album by
`album_id`, guard its existence, *then* do the stage-specific work. That shared
skeleton is a **Template Method**, owned by an abstract base so no stage repeats
it:

```python
class Stage[ResultT](ABC):  # PEP 695 generics — the py3.14 idiom, no TypeVar import
    """Template Method base for every pipeline stage. `run` owns the invariant
    envelope (load + guard the album); the variant work is `_process`."""

    def run(self, album_id: int, session: Session, *, force: bool = False) -> ResultT:
        album = self._load_album(album_id, session)
        return self._process(album, session, force=force)

    @staticmethod
    def _load_album(album_id: int, session: Session) -> Album:
        album = session.get(Album, album_id)
        if album is None:
            raise ValueError(f"no album with id {album_id}")
        return album

    @abstractmethod
    def _process(self, album: Album, session: Session, *, force: bool) -> ResultT: ...
```

Rules that keep this honest:

- **`run` stays the public surface** — the `album_id`-first, session-per-call, and
  `force`-is-keyword-only conventions (§1.2) are now enforced *by the base's
  signature*, not re-typed per stage. Tests still target `run` (§2.4).
- **`Stage` is `Generic[ResultT]`** so each subclass keeps its own concrete
  result: `class SyncStage(Stage[SyncResult])`, `_process(...) -> SyncResult`. No
  stage loses its typed return.
- **Only the genuinely invariant step is hoisted.** The album lookup + None-guard
  is universal, so it lives in the base. The *idempotency short-circuit logic* is
  **not** hoisted — it varies structurally (album-level early-return for
  `assemble`/`fulfill`, per-photo skip for `quality`/`enhance`/`caption`/`render`,
  none for `sync`/`cluster`/`select`), so each `_process` keeps its own. Hoisting a
  step that isn't actually shared is how Template Method rots into a god-method.
- **`force` is universal by *signature*, inert where there's no envelope.** The
  base's `run`/`_process` carry `*, force: bool` for every stage so the public
  surface is uniform (one Template Method, one signature). The three stages with no
  per-run idempotency — `sync`, `cluster`, `select` — accept `force` and ignore it.
  That's a deliberate trade: a uniform inherited `run` at the cost of an inert knob
  on three stages, versus per-stage `run` overrides. The *flag* being shared is not
  the same as the *short-circuit logic* being shared — only the former is.
- **A stage that needs an extra invariant guard adds it in `_process`**, not the
  base — e.g. `sync` still raises when `immich_album_id` is unset.
- **ABC here, Protocol for collaborators — they solve different problems** (§1.6.4).

`lead_faces.LeadFaceResolver` is *not* a `Stage` (it's a resolver invoked by
`select`, not a DAG stage with a `run(album_id, session)` contract) and stays a
plain class. "Where appropriate" means the shape must actually match.

#### 1.6.2 Strategy — the `Printer` boundary (already present, keep)

`fulfill` owns no delivery policy; it delegates to an injected
[`Printer`](/stages/fulfill.md) with `HtmlPrinter` / `LuluPrinter` as
interchangeable concretes. That *is* Strategy, and it predates this document —
the analysis only confirms it. Guidance going forward: when a stage grows a
second interchangeable *how* (a different selection-scoring rule, a different
scene-detection rule), extract it as an injected Strategy the same way `Printer`
was — a `Protocol` collaborator with named concretes — rather than branching on a
mode flag inside the stage. Do this only when a second concrete actually exists;
one implementation behind a Strategy interface is speculative generality.

#### 1.6.3 Singleton — rejected, on purpose

The usage analysis found no honest home for Singleton. The only global-ish state
is `config.Settings` and the engine/session factory, and both are already handled
better by the patterns this codebase is built on:

- **Dependency injection + `from_settings()` / `from_config()` factories** (§1.3)
  are the deliberate alternative. A stage receives its collaborators; it never
  reaches for a global instance.
- A Singleton's process-global, self-substituting instance is **actively hostile
  to the test-double substitution** the whole suite relies on (§2.4) — you cannot
  cleanly swap a `StubSimilarity` for the real thing if callers bind to a global.
- `pydantic-settings` already gives `Settings` a cached, single-construction
  lifecycle where one is wanted, without the pattern's coupling.

So: **do not introduce Singleton.** If a future need looks like it wants one, it
almost certainly wants an injected collaborator built once in `flow.py` instead.

#### 1.6.4 Protocol vs. ABC — when each

Both express an interface; they are not interchangeable here:

- **`Protocol` (structural) for an injected collaborator** a test substitutes —
  `SimilaritySearch`, `Printer`, `AssetSource`, `ThumbnailSource`, `Renderer`,
  `AestheticScorer`, `Embedder`, `LeadFaceProvider`. A fake satisfies it with no
  inheritance; that zero-coupling substitution is the point (§1.4). Mark
  `@runtime_checkable` **only** when `flow.py` names the type in a `@flow`
  signature (Prefect's schema build runs `isinstance`).
- **`abc.ABC` (nominal) for a shared-behaviour family** where the base supplies a
  real implementation subclasses reuse via inheritance — the `Stage` Template
  Method (§1.6.1). Reach for an ABC only when there is behaviour to *inherit*; if
  the base would be all `@abstractmethod` and no body, it should have been a
  `Protocol`.

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
  check, running inside the existing ruff pass. Ceiling **5**, matching the radon
  A boundary so it catches an over-complex function early, before the harder
  xenon gate. Scoped to **production code**: `tests/**` is exempted from `C901`
  (a `per-file-ignores` entry), because the gate governs how *production* units
  are shaped — xenon runs on `src` only, and mccabe mirrors that. Test structure
  is governed by the §2 conventions instead; an orchestration/wiring test is
  legitimately branchier than any single production method.
- **radon + xenon** — `radon` computes the metrics (cyclomatic complexity,
  maintainability index; `uv run poe metrics` prints them); **xenon** enforces
  them as a gate (`uv run poe complexity`, and inside `poe check` + pre-commit).
  Thresholds are **A across the board**: **no block worse than A** (CC ≤ 5), **no
  module averaging worse than A**, **whole-codebase average A** —
  `xenon --max-absolute A --max-modules A --max-average A src`. These three
  settings are kept in lock-step across `poe check`, the `complexity` task, and
  the pre-commit hook.

Radon grades a **class block by the mean of its method complexities** and a
**module by the mean of its blocks**, so the single discipline that satisfies all
three thresholds is: **keep every function and method at CC ≤ 5.** Do that and the
class-mean and module-mean fall out for free. There is no separate "make the class
smaller" step — there is only "no method does too much."

The gate has been tightened in steps as the structure hardened: initial
`--max-absolute C`, then **B** once the stage-class refactor split the two former
grade-C blocks, and now **A** once the `Stage` Template Method (§1.6.1) removed the
per-stage album-guard branch and the remaining grade-B methods were split into
named helpers. A new unit that can't meet the gate is the signal it's doing too
much — extract a collaborator, a helper method, or a Strategy (§1.6.2) rather than
raising the threshold. The threshold does not go back up.

## 3. Applying this standard

- **New and changed code follows it directly** — a new stage is a class from the
  first failing test; new tests are class-grouped with scoped fixtures.
- **The existing code is being retrofitted to match** (the nine stages → stage
  classes, `flow.py` wiring updated, the test suite → class-based groups + factory
  fixtures), landed as its own reviewed change, `uv run poe check` green, no
  behavioural change. The shared-analysis collaborator extraction (§1.4) is the
  one piece with real design content; everything else is mechanical.
- **The `Stage` Template Method base (§1.6.1) and the A-level gate (§2.7) are the
  current retrofit.** The nine stages inherit `Stage[…Result]` and implement
  `_process`; every method over CC 5 is split into named helpers so the whole
  codebase grades A. A helper earns its place by *naming a cohesive step*, not only
  by shaving complexity — so a few sub-steps below the ceiling were extracted too
  where it aids reading (the gate is the floor, cohesion is the goal).
  Behaviour-preserving — the existing tests (which target `run`) stay green
  unchanged, which is the guardrail. The pattern verdicts in
  §1.6 (Template Method adopted, Strategy already present, Singleton rejected) are
  standing decisions, not one-off choices.

## See also

- [Working agreement](/process/working-agreement.md) — the TDD loop and the `uv run poe check` commit gate.
- [Folder structure](/reference/folder-structure.md) — where each unit lives and the genericity/structure rules.
- [Architecture](/foundations/architecture.md) — why the collaborators are injected (idempotent stages, one combined vision call, Prefect only in `flow.py`).
- [Implementation workflow](/process/implementation-workflow.md) — the branch → review → squash → merge delivery loop this change goes through.

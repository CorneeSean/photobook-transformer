---
type: Working Agreement
title: Quality practices — code structure & testing
description: How production code is structured (classes, encapsulation, constructor-injected dependencies) and how tests are organized (class-based groups, parametrization, scoped fixtures and helpers).
tags: [process, oop, dependency-injection, testing, pytest, conventions]
timestamp: 2026-07-14
---

# Quality practices — code structure & testing

The standard for *how code is shaped*, on top of the
[working agreement](/process/working-agreement.md)'s *when-is-it-done* gate and the
[folder structure](/reference/folder-structure.md)'s *where-does-it-live* rules —
this is the authority where they overlap. Two goals: **encapsulate behaviour behind
small public surfaces**, and **keep every unit trivially testable in isolation**.

## 1. Production code structure

### 1.1 Classes vs. module functions — the hybrid rule

Lean towards **classes**. A unit becomes a class when *either* holds:
- it has **real collaborators** (an Immich client, similarity search, printer, vision model, renderer), or
- it carries **>1 private helper** or shared configuration.

A unit stays a **plain module function** only when genuinely trivial: one entry
point, no injected collaborator, ≤ a helper or two, no config. Pure value
transforms (`compute_blur_score`, `spine_width_in`, `reconcile_tone`) stay
module-level — a class around a pure function is ceremony. (Every current stage
qualifies as a class; the "function" branch is for the pure helpers *inside* a
stage module.)

### 1.2 The stage shape

A stage is constructed for **one run against one album**: injected collaborators
first, then per-run inputs (`album_id`, `session_factory`, `force`) which the base
stores. `run` takes no arguments.

```python
class SelectStage(Stage[SelectResult]):
    """`select` — diversity-aware best-of-scene via MMR."""

    def __init__(
        self,
        similarity: SimilaritySearch,
        album_id: int,
        session_factory: SessionFactory,
        *,
        force: bool = False,
        threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        super().__init__(album_id, session_factory, force=force)
        self._similarity = similarity
        self._threshold = threshold

    def _process(self) -> SelectResult: ...
    def _select_scene(self, candidates: list[Photo]) -> int: ...
```

Conventions the shape enforces:
- **A stage takes a `session_factory`, not a live `Session`; `run` takes none.** A
  `Session` isn't thread-safe, so a fan-out stage opens it **inside `run`**, on its
  worker thread — which is what makes the same instance safe to `.submit()` (§1.5).
  Sequential stages reuse the flow's shared session via a `reuse(db_session)`
  factory; fan-out stages open their own pooled one.
- **`_process` and its helpers read instance state** (`self._album`,
  `self._session`, `self._similarity`), not arguments.
- **Stable collaborators go in `__init__` first**; these are what a caller/test swaps.
- **`force` is a keyword-only construction knob**, not a `run` argument (inert where
  a stage has no idempotency).
- **The result is a `@dataclass`** returned from `run` — frozen for a pure value
  object, plain when the stage accumulates counts into it.

### 1.3 Encapsulation

- **Public surface is the smallest thing a collaborator needs** — for a stage, `run`
  plus any construction-time factory. Everything else is `_`-prefixed (including
  stored deps: `self._similarity`, not `self.similarity`) and may be reshaped freely.
- **A private helper that reads no instance state is a `@staticmethod`** — makes the
  self-independence explicit; keep it uniform across a class's helpers.
- **Prefer a factory classmethod for "the real thing".** `ImmichClient.from_settings()`:
  constructor takes explicit deps (tests pass fakes); the classmethod builds the
  production instance from `config.py`, keeping env-wiring in one place.

### 1.4 Dependency injection

- **Inject external boundaries; never reach for them.** A stage needing thumbnails
  takes a `ThumbnailSource`; it doesn't import and construct `ImmichClient`.
- **Depend on a `Protocol`, not a concrete class**, for anything a test substitutes
  (`SimilaritySearch`, `Printer`, `AssetSource`, `ThumbnailSource`, `Renderer`). A
  fake satisfies it structurally, no inheritance. Mark `@runtime_checkable` **only**
  when `flow/_flow.py` names it in the `@flow` signature (Prefect runs `isinstance`).
- **Value objects are dataclasses** passed by value (`ToneParams`, `PhotoContext`,
  `BookPage`) — frozen by default.
- **A collaborator shared by two stages is its own injected object**, not a function
  one stage imports from another — e.g. the shared `PhotoAnalyzer` both `enhance` and
  `caption` receive (the "one combined vision call", see
  [architecture](/foundations/architecture.md)).

### 1.5 `to_task`/`run_as_task` and the `flow/_flow.py` aggregator

There are **no module-level `@task` functions** — a stage *is* its own task via the
`TaskUnit` base (§1.6.1). Because a unit holds only a `session_factory`, the same
instance is safe to run inline **or** submit to a worker thread:
- **sequential** stage → `SyncStage(...reuse(db_session)).run_as_task()` (inline, reuses + commits the shared session);
- **fan-out** stage → `QualityStage(...session_factory).to_task().submit()` (worker thread opens/commits/closes its own pooled session).

`flow/_tasks.py` is **gone** (`run` owns its session + commit, so there's no shared
transaction wrapper). Wiring lives in the class-based **`Pipeline`** (`flow/_flow.py`):
its constructor takes every collaborator as a keyword defaulting to the real thing
from `config.py`, builds each stage, and composes the DAG; the public `@flow` is
`pipeline = Pipeline.flow()`, keeping `pipeline(album_slug=…, **deps)` callable and
unit-testable for wiring without touching any external service. The DAG shape,
fences, and session invariants live in
[pipeline execution](/foundations/pipeline-execution.md).

### 1.6 Design patterns — deliberate, not decorative

Patterns are adopted only where a usage analysis shows a recurring shape they name
better than ad-hoc code. Standing verdicts:

| Pattern | Verdict | Where |
|---|---|---|
| **Template Method** | Adopted | `TaskUnit` / `Stage` bases (§1.6.1) |
| **Strategy** | Already present, keep | the `Printer` boundary (`HtmlPrinter`/`LuluPrinter`); also the `ToneEstimator`/`Captioner` seams |
| **Singleton** | Rejected | DI + `from_settings()` factories replace it; a process-global is hostile to test-double substitution |
| **Protocol vs. ABC** | Both, distinct roles | Protocol (structural) for injected collaborators a test fakes; ABC (nominal) only for a shared-behaviour family with real inherited body (`Stage`) |

#### 1.6.1 Template Method — the `TaskUnit` / `Stage` bases

`TaskUnit` turns a unit into a named Prefect task (§1.5); `Stage` is the Template
Method envelope every stage's `run` shares — open a session, load + guard the album,
run the variant `_process`, flush + commit. The load + guard runs at the **top of
`run`** (not the constructor — no session exists yet at construction):

```python
class TaskUnit[ResultT](ABC):  # PEP 695 generics — the py3.14 idiom
    task_name: ClassVar[str]
    @abstractmethod
    def run(self) -> ResultT: ...
    def to_task(self) -> Task[..., ResultT]:
        return task(name=self.task_name)(self.run)
    def run_as_task(self) -> ResultT:
        return self.to_task()()


class Stage[ResultT](TaskUnit[ResultT]):
    """`run` opens the session, loads + guards the album, runs `_process`, flush+commits."""

    def __init__(self, album_id: int, session_factory: SessionFactory, *, force: bool = False) -> None:
        self._album_id = album_id
        self._session_factory = session_factory
        self._force = force

    def run(self) -> ResultT:
        with self._session_factory() as session:
            self._session = session
            self._album = self._load_album(self._album_id, session)
            result = self._process()
            session.flush()
            session.commit()
            return result

    @staticmethod
    def _load_album(album_id: int, session: Session) -> Album:
        album = session.get(Album, album_id)
        if album is None:
            raise ValueError(f"no album with id {album_id}")
        return album

    @abstractmethod
    def _process(self) -> ResultT: ...
```

Rules that keep it honest:
- **Constructor takes `album_id` + `session_factory` + `force`; `run` takes none.** A
  subclass forwards them via `super().__init__(...)` after binding its collaborators.
- **`run` owns the session lifecycle *and* flush + commit** — `flush()` before
  `commit()` surfaces integrity errors at the stage boundary. This removed each
  `_process`'s trailing flush and each old task wrapper's commit.
- **`Stage` is generic over `ResultT`** so every subclass keeps its typed return.
- **Only genuinely invariant steps are hoisted.** Album lookup + guard and flush +
  commit are universal → base. The *idempotency short-circuit* varies per stage
  (album-level for `assemble`/`fulfill`, per-photo for `quality`/`enhance`/`caption`/
  `render`, none for `sync`/`cluster`/`select`), so each `_process` keeps its own.
- **`force` is universal by signature, inert where there's no envelope** — `sync`,
  `cluster`, `select` accept and ignore it.
- **A stage needing an extra guard adds it in `_process`** (e.g. `sync` raises when
  `immich_album_id` is unset), not the base.

`lead_faces.LeadFaceResolver` is *not* a `Stage` (no `Album` envelope) but *is* a
sibling `TaskUnit`: it takes its inputs + `session_factory` at construction, runs via
`to_task().submit()`, and commits explicitly in `run`.

## 2. Testing conventions

- **Class-based groups.** Group a module's tests into `class Test…:` suites, one per
  cohesive behaviour area (happy path vs. fallbacks vs. idempotency), not a flat list.
  The class is the scoping unit — its helpers and fixtures live on or near it.
- **Parametrize within the class.** Prefer one `@pytest.mark.parametrize` (with `ids=`)
  over several near-identical tests; one assertion shape per parametrized test.

  ```python
  @pytest.mark.parametrize(
      ("raw", "expected"),
      [("Europe/Warsaw", ZoneInfo("Europe/Warsaw")),
       ("UTC+02:00", None),   # non-IANA — Immich sometimes emits these
       (None, None)],
      ids=["iana", "non-iana-offset", "missing"],
  )
  def test_resolve_zone(self, raw, expected): ...
  ```

- **Shared builders are factory fixtures in `conftest.py`.** `make_album` /
  `make_segment` / `make_photo` return a builder callable with sensible defaults + DI,
  so a test asks for exactly the builders it uses.
- **Scope a fixture to the narrowest place that needs it** — suite-wide → `conftest.py`;
  one file → that file; one class → inside the class. `scope="module"`/`"class"` only
  for expensive read-only setup (a Postgres testcontainer); pure-logic fixtures stay
  function-scoped so no state leaks.
- **Fakes over mocks, scoped to their consumer.** Substitute a `Protocol` with a small
  hand-written fake (`StubSimilarity` = real cosine in numpy), not `unittest.mock`;
  mocks that assert call sequences couple tests to internals. Define a double where
  it's used; promote to `tests/doubles.py` only once a second file needs it.
- **Test through the public surface (`run`), never private methods** — helpers are
  covered via `run`'s observable behaviour, so they can be reshaped freely. A helper
  that seems to want its own test wants to be its own public unit.
- **What doesn't change:** the [working agreement](/process/working-agreement.md)'s
  test-*strategy* table (which layer each stage is tested at) stands; this section
  governs *organization*.

### 2.7 Enforcement — the complexity gate

Structure discipline is machine-gated, not just reviewed — a too-large method fails
`uv run poe check` like a type error:
- **ruff mccabe (`C90`)** — cheap per-function smell check inside the ruff pass;
  ceiling **5** (the radon-A boundary). Scoped to production code (`tests/**` exempt).
- **radon + xenon** — radon computes metrics (`poe metrics`); **xenon** gates them
  (`poe complexity`, and in `poe check` + pre-commit) at **A across the board**:
  `xenon --max-absolute A --max-modules A --max-average A src`.

Radon grades a class by the mean of its methods and a module by the mean of its
blocks, so the single discipline is: **keep every function/method at CC ≤ 5** — the
class- and module-means then fall out for free. A unit that can't meet the gate is
doing too much: extract a collaborator, helper, or Strategy rather than raising the
threshold. **The threshold does not go back up.**

## See also

- [Working agreement](/process/working-agreement.md) — the TDD loop and `uv run poe check` gate.
- [Folder structure](/reference/folder-structure.md) — where each unit lives + genericity rules.
- [Architecture](/foundations/architecture.md) — why collaborators are injected; the one combined vision call.
- [Pipeline execution](/foundations/pipeline-execution.md) — the DAG, session ownership, and fences.

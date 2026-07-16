---
type: Reference
title: Folder structure
description: The target repository layout and the structural rules that enforce genericity and testability.
tags: [reference, layout, structure-rules]
timestamp: 2026-07-12
---

# Folder structure

```
photo-album-pipeline/
├── .gitignore
├── .dockerignore
├── .pre-commit-config.yaml   # no-commit-to-branch (protect main) + ruff + basedpyright
├── .env.example              # our Postgres + Immich compose vars, ANTHROPIC_API_KEY, IMMICH__API_KEY, LULU__*, DATABASE_URL
├── Dockerfile                # multi-stage: uv sync → runtime w/ darktable-cli
├── docker-compose.yml        # app + our Postgres + Prefect server; `include:`s the next file
├── docker-compose.immich.yml # Immich's stack, vendored from their official release compose
├── pyproject.toml            # uv deps; ruff + basedpyright + pytest + poe tasks live here
├── uv.lock
├── alembic.ini
├── README.md                 # human-readable: setup, run-locally, workflow commands
├── AGENTS.md                 # orientation for coding agents (points at knowledge/)
├── knowledge/                # OKF knowledge bundle — the project's source of truth
│
├── alembic/
│   ├── env.py
│   └── versions/
│
├── src/
│   └── album/
│       ├── __init__.py
│       ├── config.py             # pydantic-settings, reads .env
│       ├── cli.py                # `album run --album honeymoon-2026`
│       ├── definition.py         # loads/validates album.yml → Album + Segments
│       │
│       ├── flow/                 # the pipeline package (everything orchestration-related)
│       │   ├── __init__.py       # re-exports the public surface (pipeline, Pipeline, get_or_create_album) from _flow
│       │   ├── _flow.py          # thin aggregator: Pipeline class + Pipeline.flow() @flow(album_slug); imports the per-stage @tasks, composes the DAG + session/client lifecycle
│       │   ├── _tasks.py         # shared transaction-boundary wrappers (autocommit / owns_session) the per-stage @tasks run under
│       │   ├── photo_analyzer.py # PhotoAnalyzer: shared one-vision-call collaborator (enhance + caption)
│       │   ├── tone.py           # ToneParams value object + AnalysisResult/Photo conversions
│       │   │
│       │   └── stages/           # one Prefect-free Stage class per stage (run(album_id, session, …); album_id first) + that stage's own thin @task
│       │       ├── base.py       # Stage[ResultT](ABC): Template Method run() → _process hook (no Prefect import)
│       │       ├── sync.py       # SyncStage + sync_task
│       │       ├── quality.py    # QualityStage + quality_task
│       │       ├── cluster.py    # segment assignment + scene detection; ClusterStage + cluster_task
│       │       ├── select.py     # SelectStage + select_task
│       │       ├── lead_faces.py # LeadFaceResolver (helper invoked by select; not a Stage) + resolve_leads_task
│       │       ├── enhance.py    # incl. cross-camera reconciliation + XMP write; EnhanceStage + enhance_task
│       │       ├── render.py     # darktable-cli invocation; RenderStage + render_task
│       │       ├── caption.py    # CaptionStage + caption_task
│       │       ├── assemble.py   # AssembleStage + assemble_task
│       │       └── fulfill.py    # delegates to an injected printers.Printer; FulfillStage + fulfill_task
│       │
│       ├── db/
│       │   ├── models.py         # Album, Segment, Photo
│       │   ├── similarity.py     # pgvector queries, behind a substitutable interface
│       │   └── session.py        # engine + session factory from DATABASE_URL
│       │
│       ├── immich/
│       │   └── client.py         # thin typed wrapper over the Immich REST API
│       │
│       ├── lulu/
│       │   └── client.py         # thin typed wrapper over Lulu's Print API (transport only)
│       │
│       ├── printers/             # pluggable output media for the finished book
│       │   ├── base.py           # Printer protocol + BookPage reader (iter_book_pages)
│       │   ├── html.py           # HtmlPrinter — default; static page-by-page webpage
│       │   └── lulu.py           # LuluPrinter — opt-in; submits the master PDF to Lulu
│       │
│       └── agents/               # PydanticAI: output model + Agent (swap for TestModel)
│           └── analysis.py       # ONE agent; output = bounded tone deltas + caption
│
├── templates/                    # Jinja2 templates, shared across albums
│   ├── base.html                 # print-PDF page shell (assemble)
│   ├── section.html              # segment header page (assemble)
│   ├── layout_1up.html
│   ├── layout_2up.html
│   ├── layout_3up.html
│   └── book/                     # HtmlPrinter's webpage viewer
│       ├── viewer.html           # the single self-contained page shell
│       └── _components.html      # reusable presentational macros (component library)
│
├── data/                         # gitignored except album.yml files
│   └── albums/                   # (DB now lives in Postgres, not on disk here)
│       └── honeymoon-2026/
│           ├── album.yml         # the ONLY thing needed to define an album
│           └── rendered/
│
└── tests/
    ├── conftest.py               # session fixture + make_album/segment/photo factory fixtures
    ├── factories.py              # the object builders the factory fixtures wrap
    ├── doubles.py                # shared test doubles (FakeThumbnails, analysis TestModels)
    ├── pgvector_support.py       # shared pgvector testcontainer helpers
    ├── fixtures/
    │   ├── albums/               # album.yml samples: with segments, without, wedding
    │   ├── images/               # small known-blurry / known-overexposed samples
    │   ├── immich/               # recorded Immich API responses
    │   └── lulu/                 # recorded Lulu API responses
    └── album/                    # mirrors src/album/ — a test module sits at the same
        │                         # path as the code it covers, so the two navigate alike
        ├── test_config.py            # album/config.py
        ├── test_cli.py               # album/cli.py
        ├── test_definition.py        # album/definition.py — album.yml parsing/validation
        ├── test_genericity.py        # cross-cutting: the shipped wedding album bootstraps
        ├── flow/                          # mirrors src/album/flow/
        │   ├── test_flow.py               # album/flow/_flow.py — Prefect wiring, all stages faked
        │   ├── test_flow_integration.py   # real concurrent per-task sessions (pgvector testcontainer)
        │   ├── test_photo_analyzer.py     # album/flow/photo_analyzer.py — shared one-vision-call collaborator
        │   ├── test_tone.py               # album/flow/tone.py — ToneParams conversions
        │   └── stages/                    # one test module per stage
        │       ├── test_base.py
        │       ├── test_sync.py
        │       ├── test_quality.py
        │       ├── test_cluster.py        # incl. the no-segments and no-GPS fallback paths
        │       ├── test_select.py         # MMR logic, with similarity stubbed
        │       ├── test_lead_faces.py
        │       ├── test_enhance.py
        │       ├── test_render.py
        │       ├── test_render_integration.py
        │       ├── test_caption.py
        │       ├── test_assemble.py
        │       └── test_fulfill.py
        ├── db/
        │   ├── test_models.py             # Album/Segment/Photo creation + relationships, SQLite
        │   ├── test_similarity.py         # pgvector queries, against a Postgres testcontainer
        │   └── test_pgvector_integration.py
        ├── agents/
        │   └── test_analysis.py           # PydanticAI TestModel (the shared vision agent)
        ├── immich/
        │   └── test_immich_client.py      # respx against recorded fixtures
        ├── lulu/
        │   └── test_lulu_client.py        # respx against recorded fixtures
        └── printers/
            ├── test_html_printer.py
            └── test_lulu_printer.py
```

Shared helpers and fixtures live at the `tests/` root (imported absolutely, e.g.
`from tests.doubles import …`); everything under `tests/album/` mirrors
`src/album/` package-for-package. Test modules are grouped into `class Test…:`
suites with parametrization and scoped fixtures — see
[quality practices](/process/quality-practices.md) §2.

## Structure rules

- **Every stage's entry point takes `album_id` as its first argument** (a stage
  class's `run`, or a trivial stage's function). No stage may assume it's the only
  album in the database.
- **No stage may assume segments exist.** Every stage that reads segments must
  have a tested fallback path for an album with none. This is the main genericity
  guard — enforce it in tests, not just in prose.
- **Nothing in `src/` may reference trips, itineraries, days, or any
  occasion-specific concept.** If a stage needs to know an album is a trip, the
  abstraction has leaked.
- **Each `flow/stages/*.py` co-locates its own Prefect `@task` with the stage it
  wraps; `flow/_flow.py` aggregates them.** *(Changed 2026-07-16 — this reverses
  the former "stages import no Prefect; `flow/_flow.py` is the only Prefect-aware
  module" rule.)* A stage module now defines both the Prefect-free stage **class**
  (`SyncStage`, …) *and* a thin module-level `@task` over it (`sync_task`, …); the
  task takes the **constructed stage instance** as its first argument, then
  `album_id` + session/session_factory, and just calls `stage.run(...)` under a
  shared transaction-boundary wrapper from `flow/_tasks.py`. The **stage class
  stays Prefect-free logic** and remains independently unit-testable *without* a
  Prefect runtime — its tests target `run`/`_process`, never the `@task` (this is
  the testability the old rule protected, and it is preserved; importing `prefect`
  at module load is fine — it's already a dependency, and no logic test enters a
  Prefect context). `flow/_flow.py` becomes a thin **aggregator/integrator**: it
  imports the per-stage tasks, and its `Pipeline` class constructs each stage from
  the injected collaborators and composes the DAG (fan-out groups, join fences,
  session/client lifecycle) — business logic and task *definitions* live in the
  stage modules, not here. *Rationale:* a stage's orchestration wrapper reads best
  next to the stage it wraps (one file to change when a stage's inputs change), and
  DI still lives in the wiring layer because the task receives an already-built
  stage. `flow/stages/base.py` still imports **no** Prefect — the `@task`s live in
  the concrete stage modules, not the Template Method base — and nothing adds
  Prefect to `db/`, `immich/`, `lulu/`, `printers/`, `agents/`, `config.py`, or
  `definition.py`.
- **A stage is a class when it has a collaborator or several helpers; a plain
  function only when genuinely trivial** (the hybrid rule in
  [quality practices](/process/quality-practices.md) — under which all nine current stages
  are classes). Either way, the public entry point (`Stage.run` / the function)
  **takes `album_id` first, then the `Session`** — request-scoped state is never a
  constructor dependency; injected collaborators (clients, similarity, printer,
  renderer, vision model) and tunables are.
- **Every stage class subclasses `flow.stages.base.Stage[…Result]`** and implements
  `_process(album, session, *, force)`; the base's `run` is the Template Method
  that performs the invariant album lookup + existence guard, then delegates
  (quality practices §1.6.1). The stage module's own `@task` wraps the inherited
  `.run` (via a `flow/_tasks.py` transaction-boundary wrapper) — the base itself
  carries no Prefect import. A non-stage helper class (e.g.
  `flow.stages.lead_faces.LeadFaceResolver`) does **not** inherit `Stage`, and its
  co-located `resolve_leads_task` additionally forwards the resolver's extra per-run
  inputs (the parsed `lead_faces` config + its directory) that the `Pipeline` reads
  from `album.yml`.
- **`agents/*.py` define a Pydantic output model + an `Agent`**, kept out of
  `flow/stages/` so they can be swapped for `TestModel` in tests. The vision work is
  **one combined agent** producing tone deltas *and* caption in a single call.
  The shared, idempotent trigger for that call lives in one injected collaborator,
  `flow.photo_analyzer.PhotoAnalyzer`, which **both** [`enhance`](/stages/enhance.md)
  and [`caption`](/stages/caption.md) receive in their constructors — so a
  selected photo costs exactly one vision round-trip, enforced against the DB
  rather than a stage importing a function from the other.
- **`config.py` is the only place that reads env vars.**
- **`docker-compose.yml`** holds our own services (`app`, `db`, `prefect-server`)
  and pulls in Immich's stack via the top-level `include:` element rather than
  reimplementing it inline. `docker-compose.immich.yml` is a near-drop-in copy of
  Immich's official release compose, kept in its own file so a new Immich release
  can be dropped in with minimal edits. `darktable-cli` must be in the app image,
  since `flow/stages/render.py` shells out to it.

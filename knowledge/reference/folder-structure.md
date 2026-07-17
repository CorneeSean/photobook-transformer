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
│       │   ├── _flow.py          # thin aggregator: Pipeline class + Pipeline.flow() @flow(album_slug); builds each stage + runs it via run_as_task()/to_task().submit(); composes the DAG + session/client lifecycle
│       │   ├── photo_analyzer.py # PhotoAnalyzer: shared one-vision-call collaborator (enhance + caption)
│       │   ├── tone.py           # ToneParams value object + AnalysisResult/Photo conversions
│       │   │
│       │   └── stages/           # one Stage class per stage (constructed with album_id + session_factory; no-arg run()); no module-level @task functions
│       │       ├── base.py       # TaskUnit[ResultT] (task_name + to_task/run_as_task) and Stage[ResultT] Template Method (run() opens session → load+guard album → _process → flush+commit)
│       │       ├── sync.py       # SyncStage
│       │       ├── quality.py    # QualityStage
│       │       ├── cluster.py    # segment assignment + scene detection; ClusterStage
│       │       ├── select.py     # SelectStage
│       │       ├── lead_faces.py # LeadFaceResolver (invoked by select; a TaskUnit, not a Stage)
│       │       ├── enhance.py    # incl. cross-camera reconciliation + XMP write; EnhanceStage
│       │       ├── render.py     # darktable-cli invocation; RenderStage
│       │       ├── caption.py    # CaptionStage
│       │       ├── assemble.py   # AssembleStage
│       │       └── fulfill.py    # delegates to an injected printers.Printer; FulfillStage
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

- **Every stage is constructed with its `album_id` (and a `session_factory`)** and
  scopes all its work to that album. No stage may assume it's the only album in the
  database.
- **No stage may assume segments exist.** Every stage that reads segments must
  have a tested fallback path for an album with none. This is the main genericity
  guard — enforce it in tests, not just in prose.
- **Nothing in `src/` may reference trips, itineraries, days, or any
  occasion-specific concept.** If a stage needs to know an album is a trip, the
  abstraction has leaked.
- **A stage *is* its own Prefect task; there are no module-level `@task`
  functions.** *(Changed 2026-07-17 — supersedes the intermediate "each stage
  co-locates its own `@task`" rule.)* The `flow.stages.base.TaskUnit` base turns any
  unit into a named Prefect task from its class-level `task_name` and no-argument
  `run`: `run_as_task()` runs it inline (sequential stages), `to_task().submit()`
  submits it to a worker thread (fan-out stages). Because a stage holds only a
  `session_factory` (no live `Session`), the *same* instance is safe to submit — the
  session opens inside `run`, on the thread that runs it. The **stage class stays
  Prefect-free at module scope** and remains independently unit-testable *without* a
  Prefect runtime — its tests target `run`/`_process`, never `to_task`/`run_as_task`;
  `run` needs no Prefect context. `flow/_flow.py` is the thin **aggregator**: its
  `Pipeline` builds each stage from the injected collaborators (a sequential one with
  `reuse(db_session)`, a fan-out one with the pooled `session_factory`) and runs it
  via `run_as_task()` / `to_task().submit()`, composing the DAG (fan-out groups, join
  fences, session/client lifecycle). `flow/_tasks.py` no longer exists (`run` opens +
  commits its own session, so there is no shared transaction wrapper to hold).
  `prefect` is imported by `flow/stages/base.py` (the `TaskUnit` task glue) and
  `flow/_flow.py`; nothing adds Prefect to `db/`, `immich/`, `lulu/`, `printers/`,
  `agents/`, `config.py`, or `definition.py`.
- **A stage is a class when it has a collaborator or several helpers; a plain
  function only when genuinely trivial** (the hybrid rule in
  [quality practices](/process/quality-practices.md) — under which all nine current stages
  are classes). Either way, the unit is **constructed with `album_id` +
  `session_factory`** (the first per-run inputs, after the injected deps) and `run`
  takes no arguments — a live `Session` is never a constructor dependency; injected
  collaborators (clients, similarity, printer, renderer, vision model) and tunables
  are.
- **Every stage class subclasses `flow.stages.base.Stage[…Result]`** and implements
  `_process()`; the base's `run` is the Template Method that opens the session,
  performs the invariant album lookup + existence guard, delegates, then flushes +
  commits (quality practices §1.6.1). A non-stage helper class (e.g.
  `flow.stages.lead_faces.LeadFaceResolver`) does **not** inherit `Stage` but *is* a
  sibling `TaskUnit`: it takes its extra per-run inputs (the parsed `lead_faces`
  config + its directory, which the `Pipeline` reads from `album.yml`) plus a
  `session_factory` at construction, and its `run` opens the session and commits
  explicitly (no `Album` envelope).
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

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
├── .pre-commit-config.yaml   # no-commit-to-branch (protect main) + ruff + basedpyright + xenon
├── .env.example              # Postgres + Immich compose vars, IMMICH__API_KEY, ANTHROPIC_API_KEY (opt), LULU__*, DATABASE_URL
├── Dockerfile                # multi-stage: uv sync → runtime w/ darktable-cli
├── docker-compose.yml        # app + our Postgres + Prefect; `include:`s docker-compose.immich.yml
├── docker-compose.immich.yml # Immich's stack, vendored from their official release compose
├── pyproject.toml            # uv deps; ruff + basedpyright + pytest + poe tasks
├── alembic.ini
├── README.md                 # human-facing: setup, run, workflow commands
├── AGENTS.md                 # agent orientation (points at knowledge/)
├── knowledge/                # OKF bundle — the source of truth
│
├── alembic/{env.py, versions/}
│
├── src/album/
│   ├── config.py             # pydantic-settings, the ONLY reader of .env
│   ├── cli.py                # `photobook-generator` (generate <dir> | run --album <slug>)
│   ├── definition.py         # loads/validates album.yml → Album + Segments
│   ├── ingest.py             # DirectoryIngestor: dir → upload → Immich album → album.yml (generate path)
│   │
│   ├── flow/                 # orchestration package
│   │   ├── _flow.py          # aggregator: Pipeline class + Pipeline.flow() @flow; composes the DAG
│   │   ├── photo_analyzer.py # PhotoAnalyzer: shared per-photo analysis (ToneEstimator + Captioner), once per selected photo
│   │   └── stages/           # one Stage class per stage (album_id + session_factory; no-arg run(); no module-level @task)
│   │       ├── base.py       # TaskUnit[ResultT] (task_name + to_task/run_as_task) + Stage[ResultT] Template Method
│   │       └── sync·quality·cluster·select·lead_faces·enhance·render·caption·assemble·fulfill.py
│   │
│   ├── analysis/             # offline analysis collaborators (step 29)
│   │   ├── tone.py           # ToneParams value object + bounds
│   │   ├── tone_estimator.py # ToneEstimator protocol + ClassicalToneEstimator (CV, default)
│   │   ├── captioner.py      # Captioner protocol + TemplateCaptioner (offline default) / ClaudeCaptioner (opt-in)
│   │   ├── context.py        # PhotoContext + format_context (grounding assembled from album data)
│   │   └── _image.py         # shared image decode
│   │
│   ├── db/
│   │   ├── models.py         # Album, Segment, Photo, Person
│   │   ├── queries.py        # shared read helpers (e.g. selected_photos)
│   │   ├── similarity.py     # pgvector queries, behind a substitutable interface
│   │   └── session.py        # engine + session factory from DATABASE_URL
│   │
│   ├── immich/client.py      # thin typed wrapper over the Immich REST API
│   ├── lulu/client.py        # thin typed wrapper over Lulu's Print API (transport only)
│   │
│   ├── printers/             # pluggable output media
│   │   ├── base.py           # Printer protocol + BookPage reader (iter_book_pages)
│   │   ├── html.py           # HtmlPrinter — default; static page-by-page webpage
│   │   └── lulu.py           # LuluPrinter — opt-in; submits the master PDF to Lulu
│   │
│   └── agents/               # PydanticAI: segment extraction for `generate --source-document`
│       ├── segments.py       # extract_segments / extract_segments_from_pdf (swap for TestModel)
│       └── _model.py         # shared model construction
│
├── templates/                # Jinja2: base/section/layout_Nup.html (assemble) + book/ (HtmlPrinter viewer)
│
├── data/albums/<slug>/       # gitignored except album.yml (the ONLY thing needed to define an album)
│
└── tests/                    # conftest.py + factories/doubles/pgvector helpers at root; tests/album/ mirrors src/album/
```

Shared helpers/fixtures live at the `tests/` root (imported absolutely, e.g.
`from tests.doubles import …`); everything under `tests/album/` mirrors
`src/album/` package-for-package. Tests are grouped into `class Test…:` suites —
see [quality practices](/process/quality-practices.md) §2.

## Structure rules

These enforce genericity and testability. Detail and rationale for the code-shape
rules live in [quality practices](/process/quality-practices.md); this is the
checklist.

- **`album_id`-scoped.** Every stage is constructed with its `album_id` (+ a
  `session_factory`) and scopes all work to that album — never assume it's the only
  album in the DB.
- **Segments are optional.** Every stage that reads segments has a *tested* fallback
  for an album with none. The main genericity guard — enforced in tests, not prose.
- **No occasion-specific concepts in `src/`.** No trips, itineraries, or days; if a
  stage needs to know the occasion, the abstraction has leaked.
- **A stage *is* its own Prefect task.** Each stage subclasses
  `flow.stages.base.Stage[…Result]` (implements `_process()`; the base's `run` is the
  Template Method) and becomes a named Prefect task via the `TaskUnit` base — **no
  module-level `@task`, no `flow/_tasks.py`**. `run_as_task()` runs inline
  (sequential stages), `to_task().submit()` fans out (concurrent stages). The stage
  holds only a `session_factory` (never a live `Session`), so the same instance is
  safe to submit — the session opens inside `run`, on the thread that runs it. Full
  design + the session/threading invariants: [quality practices](/process/quality-practices.md)
  §1.5–1.6 and [pipeline execution](/foundations/pipeline-execution.md).
- **A stage is a class when it has a collaborator or several helpers**, a plain
  function only when genuinely trivial (the hybrid rule — all current stages are
  classes). `LeadFaceResolver` is a non-`Stage` sibling `TaskUnit`.
- **`prefect` is imported only by `flow/stages/base.py` and `flow/_flow.py`** —
  never in `db/`, `immich/`, `lulu/`, `printers/`, `analysis/`, `agents/`,
  `config.py`, or `definition.py`.
- **One combined per-photo analysis.** [`enhance`](/stages/enhance.md) and
  [`caption`](/stages/caption.md) both receive the shared `flow.photo_analyzer.PhotoAnalyzer`
  (tone deltas + caption, idempotent, once per selected photo) — see
  [architecture](/foundations/architecture.md).
- **`config.py` is the only place that reads env vars.**
- **`docker-compose.yml`** holds our own services (`app`, `db`, `prefect-server`)
  and pulls in Immich's stack via `include:`. `darktable-cli` must be in the app
  image, since `flow/stages/render.py` shells out to it.

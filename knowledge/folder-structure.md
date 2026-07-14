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
├── .env.example              # our Postgres + Immich compose vars, ANTHROPIC_API_KEY, IMMICH_API_KEY, LULU_*, DATABASE_URL
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
│       ├── flow.py               # Prefect @flow(album_slug); wraps stages as @tasks
│       ├── definition.py         # loads/validates album.yml → Album + Segments
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
│       ├── agents/               # PydanticAI: output model + Agent (swap for TestModel)
│       │   └── analysis.py       # ONE agent; output = bounded tone deltas + caption
│       │
│       └── stages/               # pure functions; all take album_id first
│           ├── sync.py
│           ├── quality.py
│           ├── cluster.py        # segment assignment + scene detection
│           ├── select.py
│           ├── enhance.py        # incl. cross-camera reconciliation + XMP write
│           ├── render.py         # darktable-cli invocation
│           ├── caption.py
│           ├── assemble.py
│           └── fulfill.py            # delegates to an injected printers.Printer
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
    ├── conftest.py               # in-memory SQLite + Album/Segment/Photo factories
    ├── fixtures/
    │   ├── albums/               # album.yml samples: with segments, without, wedding
    │   ├── images/               # small known-blurry / known-overexposed samples
    │   └── immich/               # recorded API responses
    ├── test_models.py            # Album/Segment/Photo creation + relationships, in-memory SQLite
    ├── test_definition.py        # album.yml parsing/validation
    ├── test_cluster.py           # incl. the no-segments and no-GPS fallback paths
    ├── test_select.py            # MMR logic, with similarity stubbed
    ├── test_similarity.py        # pgvector queries, against a Postgres testcontainer
    ├── test_quality.py
    ├── test_immich_client.py
    ├── test_agents.py            # PydanticAI TestModel
    └── test_assemble.py
```

## Structure rules

- **Every stage function takes `album_id` as its first argument.** No stage may
  assume it's the only album in the database.
- **No stage may assume segments exist.** Every stage that reads segments must
  have a tested fallback path for an album with none. This is the main genericity
  guard — enforce it in tests, not just in prose.
- **Nothing in `src/` may reference trips, itineraries, days, or any
  occasion-specific concept.** If a stage needs to know an album is a trip, the
  abstraction has leaked.
- **`stages/*.py` contain plain functions with no Prefect imports.** `flow.py` is
  the only module that knows Prefect exists — it wraps stage functions as
  `@task`s. Keeps business logic unit-testable without an orchestrator, which is
  what makes the TDD loop fast.
- **`agents/*.py` define a Pydantic output model + an `Agent`**, kept out of
  `stages/` so they can be swapped for `TestModel` in tests. The vision work is
  **one combined agent** producing tone deltas *and* caption in a single call;
  [`enhance`](/stages/enhance.md) and [`caption`](/stages/caption.md) consume its
  persisted output rather than each calling the model, so a selected photo costs
  exactly one vision round-trip.
- **`config.py` is the only place that reads env vars.**
- **`docker-compose.yml`** holds our own services (`app`, `db`, `prefect-server`)
  and pulls in Immich's stack via the top-level `include:` element rather than
  reimplementing it inline. `docker-compose.immich.yml` is a near-drop-in copy of
  Immich's official release compose, kept in its own file so a new Immich release
  can be dropped in with minimal edits. `darktable-cli` must be in the app image,
  since `stages/render.py` shells out to it.

# Directory Update Log

## 2026-07-13

* **Build step 3 done** (commit `84260ef`): added `src/album/config.py` — the
  first (and only) module that reads env vars, a pydantic-settings `Settings`
  with `env_nested_delimiter="__"` configured up front so grouped settings
  added in later steps (e.g. a future `LULU__CLIENT_KEY`) don't need this file
  touched again — and `src/album/definition.py`
  (`load_album_definition(path) -> Album`), which parses
  [`album.yml`](/album-definition.md) via Pydantic (`AlbumYAML`/
  `SegmentYAML`) into unpersisted `Album`/`Segment` ORM rows, normalizing
  every segment time to UTC: an explicit offset wins, otherwise
  `Album.timezone` is used, otherwise it raises. `alembic/env.py` now reads
  `DATABASE_URL` via `config.py` instead of `os.environ` directly, closing
  out the comment left at step 2. Test-first (`tests/test_definition.py`,
  `tests/test_config.py`, 13 new tests): trip-shaped, wedding-shaped, and
  segment-less fixtures all load correctly; an offset-based and an
  equivalent album-timezone-based fixture land on the same UTC instant;
  missing-timezone, overlapping, and malformed-range fixtures raise
  `ValidationError`. Sub-agent review (8 finder angles) caught and fixed two
  live-reproducible bugs: an unknown IANA timezone name leaked a raw
  `zoneinfo.ZoneInfoNotFoundError` instead of a clean `ValidationError`, and
  a naive local time landing in a DST spring-forward gap (e.g. `01:30` on
  the day UK clocks jump `01:00`→`02:00`) was silently converted using an
  inconsistent UTC offset instead of raising — both now have regression
  fixtures and raise clearly. The review also flagged that `alembic/env.py`
  now hard-requires a resolvable `DATABASE_URL` (previously it fell back
  silently to `alembic.ini`'s static URL); kept as intentional, per the
  step-2 comment anticipating exactly this, and clarified with a comment
  in `alembic.ini`.

* **Build step 2 done** (commit `68ed96f`): added `src/album/db/models.py`
  (`Album`/`Segment`/`Photo` SQLAlchemy 2.0 ORM, mirroring
  [data-model.md](/data-model.md) exactly) and the first alembic migration,
  which runs `CREATE EXTENSION IF NOT EXISTS vector` before creating the
  `pgvector` `clip_embedding` column. Test-first: an in-memory SQLite fixture
  (`tests/conftest.py`) with an album that has segments and a segment-less
  album, both created and related correctly (`tests/test_models.py`).
  Verified the migration's upgrade/downgrade/upgrade round-trip against a
  real Postgres (`pgvector/pgvector:pg16`). Sub-agent review (8 angles, 2
  rounds) caught and fixed one real issue: `uv run poe migrate`/
  `makemigration` never loaded `.env` when run outside Docker, so a
  customized `DATABASE_URL` silently fell back to `alembic.ini`'s
  placeholder — fixed via poethepoet's `envfile = ".env"`.

* **Build step 1 done** (commit `240fbe0`): added the multi-stage `Dockerfile`
  (uv sync builder → runtime with `darktable-cli`) and `docker-compose.yml`
  (our `app` + our own `pgvector/pgvector` Postgres, kept separate from
  Immich's + a Prefect 3 server). **Decision**: at the user's request, Immich's
  stack is vendored into its own `docker-compose.immich.yml` (kept close to
  Immich's official release compose) rather than inlined, and pulled into
  `docker-compose.yml` via Compose's `include:` element — a new Immich release
  is close to a drop-in file swap. Added `.env.example` and `.dockerignore`.
  Verified `docker compose up --build` brings all services healthy, Immich's
  and Prefect's web UIs both respond `200`, `darktable-cli --version` runs
  inside the app image, and `CREATE EXTENSION vector;` succeeds on our own
  Postgres. Sub-agent review (3 parallel angles) caught and fixed: `db`'s host
  port is now overridable via `APP_DB_HOST_PORT` (avoids colliding with an
  existing local Postgres), `poe up` now passes `--build` so a changed
  Dockerfile/source isn't served stale, and two doc/comment accuracy issues.

## 2026-07-12

* **Creation**: Transformed `PROJECT_BRIEF.md` into this OKF v0.1 knowledge
  bundle — split into per-concept documents (overview, domain model,
  architecture, working agreement, data model, album definition, tech stack,
  folder structure, build order, open questions) and a `stages/` subdirectory
  with one document per pipeline stage.
* **Decision**: Adopted **basedpyright** as the static type checker (added to the
  tech stack, the commit gate, and build step 0), with **Astral `ty`** documented
  as the planned future migration once it reaches a stable release.
* **Decision**: Encoded the manual workflow as **poethepoet** tasks runnable via
  `uv run poe <task>`, and required a human-readable `README.md` plus an
  `AGENTS.md` at scaffold time (build step 0).
* **Decision**: Resolved the `enhance`/`caption` contradiction — a **single
  combined vision agent** (`agents/analysis.py`) produces tone deltas *and* the
  caption in one persisted, idempotent call; both stages consume its output, so a
  selected photo costs exactly one vision round-trip.
* **Decision**: Settled timezone handling — all datetimes are stored tz-aware in
  **UTC** (`DateTime(timezone=True)`); `album.yml` carries explicit offsets or an
  album-level `timezone`; `definition.py` and `sync` normalize to UTC. The
  remaining open question is narrowed to determining each photo's source zone
  from EXIF/Immich (Open Question #3).
* **Process**: Added the per-step [implementation workflow](/implementation-workflow.md)
  (branch per step → TDD → sub-agent review → user review → squash → fast-forward
  merge; never commit to `main`) and a living [progress tracker](/progress.md).
* **Decision**: Added local `pre-commit` hooks at scaffold — `no-commit-to-branch`
  to protect `main`, plus ruff and basedpyright — with the full pytest suite left
  to `uv run poe check` and CI.
* **Build step 0 done** (commit `11d290f`): scaffolded the `uv` project (Python
  3.14, `album` console entry point), ruff/basedpyright/pytest + poethepoet
  tasks in `pyproject.toml`, the minimal package skeleton (`db/`, `immich/`,
  `agents/`, `stages/` sub-packages and a `cli.py` stub — `config.py`,
  `definition.py`, `flow.py` deliberately deferred to steps 3 and 14),
  `.gitignore`, `.pre-commit-config.yaml` (installed), and `README.md`.
  Verified `uv run poe check` green and a direct commit to `main` is rejected
  by the `no-commit-to-branch` hook. Sub-agent review caught and fixed a
  `.gitignore` pattern that failed to exclude files nested under
  `data/albums/<slug>/` and a leftover `uv init` template stub in
  `src/album/__init__.py`.

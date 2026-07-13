# Directory Update Log

## 2026-07-13

* **Build step 7 done** (commit `2a39df5`, `cluster` stage): added
  `src/album/stages/cluster.py` — assigns each photo a `segment_id` (matching its
  UTC timestamp against the album's ordered, non-overlapping segments) and a
  `scene_id`. Scene detection is **temporal** (a time gap over ~90 min starts a
  new scene) by default, and takes the **spatial** path (scikit-learn DBSCAN over
  lat/lon, haversine, `min_samples=1`) when every photo in a group has GPS — a new
  scene begins when the time gap breaks *or* the spatial cluster changes between
  time-adjacent photos. **Genericity guard, tested:** an album with no segments
  falls back to one implicit segment; one with no GPS falls back to pure time-gap
  scenes. Photos outside every segment are flagged in `ClusterResult.out_of_range`
  (not dropped); NULL-timestamp photos are flagged in `.undated` and left
  unassigned. Pure logic, no Prefect, idempotent (resets `segment_id`/`scene_id`
  up front). Added scikit-learn (+ scipy). Test-first (10 tests): the GPS test
  genuinely forces the spatial path (four photos <30 min apart at Lisbon vs Porto
  → two scenes, would fail if spatial detection were removed). Sub-agent review
  confirmed no functional bug; applied its two suggestions — `.order_by(Photo.id)`
  so the result lists are deterministic on Postgres, and a documented
  inclusive-both-ends / first-match segment-boundary convention — plus two added
  tests (spatial+temporal revisit, mixed-GPS temporal fallback).

* **Build step 6 done** (commit `a6c4b9e`, `quality` stage): added
  `src/album/stages/quality.py` — scores each photo's blur and aesthetics,
  scoped by `album_id`, writing back to the `Photo` row. **Blur** is real:
  Laplacian variance (OpenCV) over the Immich thumbnail. **Aesthetic** scoring
  and **CLIP embedding** are injected behind `AestheticScorer`/`Embedder`
  protocols (the LAION predictor / open_clip / torch stack is heavy and
  model-weight-dependent), so the stage stays unit-testable with stubs — the
  same seam pattern as [`sync`](/stages/sync.md)'s Immich client. Thumbnails
  come via a narrow `ThumbnailSource` protocol that `ImmichClient.get_thumbnail`
  satisfies. Idempotent (skips fully-scored photos unless `force`), with
  per-photo failure isolation. Added `opencv-python-headless` + `numpy` +
  `pillow`. Test-first (8 tests) on in-memory SQLite: real blur ranks a **sharp
  fixture above a pre-blurred copy of the same source image** (downloaded from
  Lorem Picsum — sharp var 788 vs blurry 3.6) end-to-end through `quality()`;
  scores persist; idempotency + `force`; album scoping; embedder path persists a
  768-dim vector (SQLite round-trips vector *storage* fine — only similarity
  *queries* need Postgres). **Deferred:** the real aesthetic/embedding model
  implementations (open_clip/LAION) aren't shipped — wired via injection, to be
  supplied when the flow lands (step 14). Sub-agent review caught one real bug:
  a mid-photo failure previously left a stray `blur_score` that the trailing
  `session.flush()` still persisted (partial write despite being counted
  `failed`) — fixed by computing all scores into locals and assigning to the
  `Photo` only once every computation succeeds; regression test added.

* **Build steps 4 + 5 done** (delivered together on one branch/review at the
  user's request; `sync` stage + Immich client): added
  `src/album/immich/client.py` — a thin, typed `httpx` wrapper over the Immich
  REST API (`ImmichClient` + `ImmichAsset`/`ImmichExif`/`ImmichDuplicate` pydantic
  models) covering album-asset listing (paginated `POST /search/metadata`), asset
  EXIF (`GET /assets/{id}`), duplicate groups (`GET /duplicates`), and thumbnail
  fetch — and `src/album/stages/sync.py` (`sync(album_id, session, client)`),
  the idempotent Immich→DB boundary: upserts `Photo` rows keyed on Immich asset id,
  scoped to `album_id`, normalizing each capture time to UTC (aware
  `dateTimeOriginal` wins → else `exifInfo.timeZone` → else `Album.timezone` →
  else flagged ambiguous with the timestamp left NULL). Added `db/session.py`
  (lazy engine/session factory from `DATABASE_URL`) and two `config.py` settings
  (`immich_base_url`, `immich_api_key`, defaulted so DB-only flows don't need
  them). Test-first (14 new tests): client tests use `respx` against
  schema-accurate fixtures under `tests/fixtures/immich/`; `sync` tests run on
  in-memory SQLite with a fake `AssetSource`, covering idempotency, `album_id`
  scoping, the three timezone resolution paths, and the ambiguous-flag path.
  Added `httpx` + `respx` deps.
    * **Open question #1 resolved:** the Immich REST API (OpenAPI v3.0.2) exposes
      **no CLIP embeddings** — grep of the spec finds zero `embedding` fields.
      `quality` will compute its own via `open_clip`. Recorded in the new
      [Immich API notes](/immich-api.md); also captured a partial answer to
      open question #3 (Immich *does* surface `exifInfo.timeZone`).
    * **Manual/live verification intentionally skipped** (per user): the live
      Immich stack was unreachable (port 2283 refused, `IMMICH_API_KEY` empty),
      so fixtures are derived from the OpenAPI contract rather than a live
      capture, and step 5's "run for real against the honeymoon album" was not
      performed. Both are flagged in `immich-api.md` to re-verify on the first
      real sync.
    * **Sub-agent review (2 angles) caught a genuine blocker:** the committed
      `sync.py` contained `except ZoneInfoNotFoundError, ValueError:` (a hard
      `SyntaxError`). Root cause is a **`ruff` 0.15.21 formatter bug**: under
      `target-version = "py314"` (which this repo uses) `ruff format` rewrites a
      valid `except (A, B):` into the invalid paren-less form, and there is no
      fixed release yet (0.15.21 is latest). This creates a catch-22 —
      `ruff format --check` demands the broken form while Python/basedpyright/
      pytest reject it — so **avoid parenthesized multi-type `except` clauses**
      (use separate `except` clauses, or `# fmt: skip`) until ruff ships a fix.
      `sync._resolve_zone` was rewritten to two separate `except` clauses.

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

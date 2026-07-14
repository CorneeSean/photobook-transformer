# Directory Update Log

## 2026-07-14

* **Build step 11 done** (commit `168940f`, `render` stage): added
  `src/album/stages/render.py` (`darktable-cli` batch invocation over the XMP
  sidecars `enhance` writes) and `ImmichClient.get_original()` — a real gap:
  the client only exposed thumbnails before, but `darktable-cli` needs the
  actual source pixels (XMP carries only edit instructions), documented in
  `knowledge/immich-api.md`'s endpoint table. Fetches each selected photo's
  original, stages it locally, invokes an injected `Renderer` protocol
  (`DarktableRenderer` shells out via `subprocess.run` in the real path),
  and sets `Photo.rendered_path` on success; a selected photo without an XMP
  sidecar yet (`xmp_written=False`) is counted as `not_ready` rather than
  silently dropped, mirroring `enhance.py`'s `unclustered` handling. Built in
  parallel with step 12 (`caption`) once step 10 unblocked both. Self-review
  (code-review skill) caught and fixed: no check that the renderer actually
  produced an output file (trusted a "raise on failure" contract blindly);
  the staged original was re-fetched from Immich on every retry instead of
  reused; the staged original was never cleaned up, growing disk use
  unboundedly — fixed to delete it after a successful render. Deferred: the
  manual "visibly corrected JPEG" check (no `darktable-cli` on this host,
  only in the Docker app image). ruff + basedpyright + pytest green, 146
  tests (131 pre-existing + 15 new: 14 in `tests/test_render.py`, 1 for
  `get_original` in `tests/test_immich_client.py`).
* **Build step 12 done** (commit `69f82b3`, `caption` stage): added
  `src/album/stages/caption.py` — a thin stage whose only job is to iterate
  an album's `selected` photos and call `enhance.ensure_analysis()` (step
  10's shared idempotent "one combined vision call" trigger) for each,
  reusing rather than reimplementing its idempotency. Order-independent with
  `enhance`: whichever stage runs first triggers the one shared API call per
  photo. Per-photo failure isolation (bad thumbnail, unparseable model
  response) mirrors `quality.py`/`enhance.py`. Built in parallel with step 11
  (`render`) — see that entry once merged. Deferred: the manual ~20-caption
  accuracy spot-check (no `ANTHROPIC_API_KEY`). ruff + basedpyright + pytest
  green, 131 tests (121 pre-existing + 10 new).
* **Parallel build: steps 10 (`enhance`), 13 (`assemble`), 15 (`fulfill`)
  built concurrently**, per user, on separate git worktrees/branches, since
  they share no files or logic (confirmed by dependency analysis: 11 `render`
  and 12 `caption` genuinely depend on 10, so weren't parallelized). Each
  agent self-administered a `code-review` pass before handing off; a
  coordinator merged all three serially, reconciling the one real conflict —
  `assemble` and `fulfill` each independently added an `Album` column
  (`assembled_pdf_path` and `lulu_order_id`), so `fulfill`'s placeholder
  `assembled_pdf_path` migration/column was dropped in favor of `assemble`'s
  real one, and `fulfill`'s `lulu_order_id` migration was rebased to chain
  onto it.
* **Build step 10 done** (commit `e7fccfe`, `enhance` stage): added
  `src/album/stages/enhance.py` — `ensure_analysis()`, the shared idempotent
  "one combined vision call" trigger (persists tone deltas + caption together,
  skips if already populated) that `caption` (step 12) should reuse; cross-camera
  exposure baseline via `skimage.exposure.match_histograms` per scene
  (earliest-timestamp photo as reference), combined with the AI's per-photo
  deltas on top; writes darktable-native XMP sidecars (`darktable:` namespace)
  to `data/albums/<slug>/xmp/`, which `render` (step 11) will feed to
  `darktable-cli`. Self-review (code-review skill, 8-angle) caught and fixed:
  a force-re-run idempotency bug that compounded the camera baseline on every
  forced re-run; selected-but-unclustered photos vanishing silently (now
  counted + logged); a `camera_model` None/None false-match treating two
  cameras as the same. Deferred: live-Claude sanity check (no
  `ANTHROPIC_API_KEY`, same pattern as step 9). ruff + basedpyright + pytest
  green, 94 tests (74 pre-existing + 20 new).
* **Build step 13 done** (commit `9ec5ca7`, `assemble` stage): added
  `src/album/stages/assemble.py` + `templates/{base,section,layout_1up,
  layout_2up,layout_3up}.html`. Resolved Open Q4 (Lulu's trim/bleed/spine
  spec, fetched from Lulu's public OpenAPI docs on 2026-07-14): 8.5x8.5in
  square trim, 0.125in bleed, spine width = page_count/444 + 0.06in
  (softcover perfect-bound). Reads `selected=True` photos, groups by
  segment/scene/timestamp, cycles them through 1up/2up/3up layouts (densest
  first), renders via WeasyPrint to `<slug>/assembled.pdf`, and stores the
  path on the new `Album.assembled_pdf_path` column (own migration,
  `9e650f717355`) — the contract `fulfill` (step 15) reads. Segment-less
  album fallback tested (genericity guard). Structural PDF assertions
  (page count, page-box dims via `pypdf`) stand in for the build order's
  manual "open it and look" check. Self-review (code-review skill, 8-angle,
  run by the coordinator after the building agent hit a session limit
  mid-self-review — see the parallel-build note above) caught and fixed:
  `<img>` paths resolved against the templates dir instead of the process's
  cwd (masked by every test fixture using an absolute path); the
  zero-selected-photos fallback PDF had no `@page` size rule, defaulting to
  WeasyPrint's A4 instead of the Lulu spec; a selected photo with no
  `rendered_path` yet silently produced `<img src="None">` instead of
  raising; an O(photos×segments) lookup that ignored an already-built O(1)
  dict; a redundant second full-table query for the idempotency reset; a
  dead fallback branch in the layout-size lookup. ruff + basedpyright +
  pytest green, 108 tests (94 pre-existing + 14 new).
* **Build step 15 done** (commit `92a7d4c`, `fulfill` stage): added
  `src/album/lulu/client.py` (`LuluClient` — OAuth2 client-credentials auth,
  `POST /print-jobs/`, `GET /print-jobs/{id}/`) and
  `src/album/stages/fulfill.py` (`fulfill()` + `LuluPrintJobAdapter`, which
  keeps fulfillment policy — recipient, SKU, shipping level — out of the thin
  HTTP client). Reads `Album.assembled_pdf_path`, submits it, stores the
  returned id on `Album.lulu_order_id` (own migration). Researched against
  Lulu's public OpenAPI spec (fetched 2026-07-14): confirmed the
  client-credentials token endpoint and `POST /print-jobs/` request/response
  shape; `GET /print-jobs/{id}/`'s exact response schema was inferred, not
  directly confirmed. Self-review (code-review skill, 8-angle) caught and
  fixed: fulfillment policy relocated out of the HTTP client into the stage;
  `submit()` now rejects non-`http(s)` PDF paths explicitly rather than
  forwarding a local path to Lulu unchecked. Built in parallel with `assemble`
  (step 13) — see the parallel-build note above for how the coordinator
  reconciled the two branches' independent `Album.assembled_pdf_path`
  migrations after both landed. Flagged gap: no PDF-hosting/upload mechanism
  exists yet, so a real submission would fail loudly (`UnreachablePdfError`)
  rather than send Lulu an unfetchable local path — follow-up work for
  whoever wires the real flow (step 14) or first live order. Manual
  single-copy test-order verification deferred (no Lulu sandbox credentials).
  ruff + basedpyright + pytest green, 121 tests (108 pre-existing + 13 new).
* **Closed step 9's deferred Docker/testcontainer re-verification.** Docker
  Desktop was reinstalled, fixing the environment flakiness noted below (step 9
  entry). `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check` now passes clean:
  ruff, format, basedpyright, and all 74 pytest tests (including
  `tests/test_similarity.py`'s pgvector testcontainer). No code changes needed —
  `main` was already in the state left by step 9.

## 2026-07-13

* **Build step 9 done** (commit `38a4057`, agent `analysis.py`): added
  `src/album/agents/analysis.py` — the single combined PydanticAI vision agent
  from architecture.md's "one combined vision call": one request per selected
  photo produces both bounded tone-correction deltas (`exposure_ev`,
  `temp_shift_k`, `shadows`, `highlights`, all `Field(ge=, le=)`) and a
  caption, in one `AnalysisResult`. `PhotoContext` carries whatever grounding
  the album has (segment label/place, `Album.meta`) — all optional; a photo
  with none of it still gets a caption via `format_context`'s generic
  fallback. Deliberately DB-free/side-effect-free — no `Session`, no
  `album_id`, no persistence or idempotency check, left to the calling stages
  (`enhance` step 10, `caption` step 12), mirroring `immich/client.py` (pure
  API wrapper) vs. `stages/sync.py` (owns the DB). The `Agent` is built
  without a bound model so importing the module never requires
  `ANTHROPIC_API_KEY`; `analyze_photo()` defaults to a real Claude model built
  from `config.py`'s new `anthropic_api_key` setting, or accepts an injected
  `TestModel` for tests. Added `pydantic-ai-slim[anthropic]`. Test-first (10
  tests) via `TestModel` — no real Claude calls: bounds/empty-caption
  rejection (parametrized), a valid end-to-end result, the no-segment-context
  path, and `format_context`'s interpolation.
    * **Sub-agent review (8 parallel finder angles) caught a real bug,
      confirmed independently by 3 angles:** `_default_model()` built a fresh
      `AnthropicProvider` (and its own `httpx.AsyncClient`/connection pool) on
      *every* `analyze_photo()` call, never closed — a real leak across a
      300-500-photo album run, and it threw away HTTP connection reuse each
      time too. Fixed by caching it with `@cache`, the same pattern
      `db/session.py`'s `get_engine()` already uses. Also fixed:
      `AnalysisResult.caption` had no `min_length`, so an empty string passed
      validation despite the class docstring's claim that malformed responses
      are rejected. Parametrized the four near-identical bounds-rejection
      tests per a simplification finding.
    * **Deferred (no `ANTHROPIC_API_KEY`):** the build order's "wire in real
      Claude calls and sanity-check ~20 photos" manual step — same deferral
      pattern as steps 4-5's live-Immich check. To do once a key is
      available.
    * **Environment flakiness:** Docker Desktop went fully unresponsive in
      this sandbox mid-step (beyond the step-8 Ryuk landmine — even
      `docker ps` hung for minutes), so `tests/test_similarity.py` (step 8's
      pgvector testcontainer test, untouched by this step) couldn't be
      re-verified as part of this step's final check; `pytest
      --ignore=tests/test_similarity.py` plus lint/typecheck were used
      instead (70/70 passed). Re-run the full `uv run poe check` once Docker
      is confirmed responsive.

* **Build step 8 done** (commit `4fec4a1`, `select` stage): added
  `src/album/stages/select.py` and `src/album/db/similarity.py`. `select`
  implements diversity-aware best-of-scene via MMR: within each scene
  (`Photo.scene_id`, from `cluster`), candidates are ranked by
  `aesthetic_score` and greedily picked, skipping any candidate that's an
  Immich-flagged duplicate (`duplicate_group_id`) of an already-picked photo,
  or whose CLIP cosine similarity to an already-picked photo exceeds a 0.9
  threshold. Unclustered photos (`scene_id IS NULL`) are never selected.
  Idempotent (resets `selected` for the album up front). The `pgvector`
  cosine-distance query sits behind a `SimilaritySearch` protocol
  (`db/similarity.py`), so MMR selection logic is unit-tested on in-memory
  SQLite with similarity stubbed; the real `PgVectorSimilarity` query is
  covered separately against a **Postgres testcontainer**
  (`pgvector/pgvector:pg17`) — the first use of `testcontainers-python` in
  this repo, added as a dev dependency. Test-first (16 new tests): highest
  score wins, near-duplicates skipped, Immich duplicates skipped, an
  all-similar scene yields exactly one selection, a diverse scene yields
  several, multi-scene independence, idempotency, album scoping.
    * **Sub-agent review (8 parallel finder angles) caught a real, confirmed
      bug:** `Photo.clip_embedding` is nullable by design (the `Embedder` is
      optional in `quality`, and per-photo failures there leave it unset), but
      `select()` called into the real similarity query unconditionally —
      crashing the whole album's selection with an unhandled `ValueError`
      (candidate missing an embedding) or `TypeError` (an already-picked photo
      missing one, so pgvector's `cosine_distance` returned SQL `NULL` and
      Python's `min()` choked on it). Fixed by changing `SimilaritySearch.
      max_similarity`'s signature to take the candidate's embedding directly
      (already in memory on the loaded `Photo`, cutting a redundant DB round
      trip too) instead of re-fetching it by id, and by filtering `NULL`
      embeddings out of the real query's "already-picked" side — both paths
      now return `-1.0` ("nothing to compare against") rather than raising.
      Re-reviewed clean after the fix (4 new regression tests added, 2 per
      test file).
    * **Toolchain landmine:** in this dev sandbox, `testcontainers`' Ryuk
      reaper container can't start (Docker socket mount denied) — tests need
      `TESTCONTAINERS_RYUK_DISABLED=true`; `PostgresContainer` also defaults
      to a `psycopg2` URL, so `driver="psycopg"` must be passed explicitly
      (this project is on psycopg3). Recorded in progress.md.

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

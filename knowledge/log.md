# Directory Update Log

## 2026-07-17

* **Ruff trailing commas + `SimilaritySearch` → `Sequence` (user-directed,
  not a build step).** Widened `db/similarity.py`'s
  `SimilaritySearch`/`PgVectorSimilarity`/`StubSimilarity` inputs to
  `Sequence[float]`/`Sequence[int]` (read-only seam, consistent with
  `selected_photos`; commit `110c35a`). Enabled `COM812` and applied a repo-wide
  trailing-comma reformat (52 files; commit `ae8f4dd`) so multi-line
  collections stay vertical. Ruff's advisory that `COM812` "may conflict with
  the formatter" led to moving it to `lint.ignore` (commit `de49b50`) — the
  reformat stays and the formatter's magic trailing comma maintains vertical
  collections, but trailing commas are no longer linter-enforced. `uv run poe
  check` green throughout (293 pass / 3 skip). Recorded in
  [progress.md](/build/progress.md) Notes.

* **Open question 7 resolved — shared `selected_photos` query hoisted**
  (new `src/album/db/queries.py` + `tests/album/db/test_queries.py`;
  [open-questions.md](/build/open-questions.md) Q7 marked resolved). The
  "selected photos for this album, ordered by id" read, previously copied in
  `caption`/`enhance`/`render`, is now a single
  `selected_photos(album_id, session)` free function (option A — a
  query-functions module, chosen over a Repository framework: SQLAlchemy's
  Session already provides Unit of Work / Identity Map, so a repository layer
  would be redundant for a 4-model pipeline and would fight the step-25 per-task
  session model). The function takes the caller's session and never owns one, so
  it composes with the fan-out session ownership. Fixed the latent drift the Q7
  note warned about — `caption` was missing the `.order_by(Photo.id)` the other
  two stages had (order-independent processing, so determinism only, no behavior
  change). `uv run poe check` green (293 passed, 3 skipped).

* **Proposal recorded — offline analysis (classical replacement for the Claude
  vision call)** (new [local-analysis.md](/build/local-analysis.md); added as
  outstanding step 29 in [progress.md](/build/progress.md), `todo (needs
  planning)`; linked from [index.md](/index.md)). Captured a design proposal to
  drop the pipeline's last external-API dependency by splitting the one Claude
  call into two classical jobs: tone deltas via computer vision (auto-exposure /
  AWB / histogram) and the grounded caption via metadata templating from
  who/where/when already in the DB (reusing the existing grounding helpers),
  keeping Claude / a local VLM injectable as an optional "rich caption" mode
  (mirrors the printer abstraction). Not cost-driven (~$1–$4/album) — offline /
  no-key / deterministic. **Marked as needing a proper brainstorming + planning
  pass before implementation;** open questions (template grammar, seam,
  tone→XMP mapping, validation) listed in the proposal. Independent of step 23.

* **Open question 5 resolved — AI-pass cost estimate** (new
  [ai-cost-estimate.md](/build/ai-cost-estimate.md); [open-questions.md](/build/open-questions.md)
  Q5 marked resolved; linked from [index.md](/index.md)). Estimated the full
  Claude vision pass from the pinned agent code (`agents/analysis.py`,
  `agents/_model.py` → `claude-sonnet-5`; one combined, idempotent call per
  *selected* photo via `PhotoAnalyzer.ensure`) and current published pricing:
  ≈ **$1–$4 per album** of 300–500 photos (small Immich thumbnail ≈ 60 image
  tokens/call; ~$0.66–$1.65 at the intro $2/$10 rate, ~$4 worst case with a
  larger image at standard $3/$15). Verdict: **acceptable — no cost control
  needed before the first run.** Prompt caching not worth it (system prompt below
  Sonnet's 2,048-token cache floor; image differs per call); Batch API would
  halve it but the absolute cost is trivial. Metered re-check deferred to the
  first live run (no `ANTHROPIC_API_KEY` in this environment — same deferral as
  steps 9/10/12).

* **TestContainers step 24 done — offline Immich ML integration via a pre-baked
  model image** (commit `ad86550`, branch `step-24-immich-ml-integration`;
  [testcontainers.md](/build/testcontainers.md) step 24). User-directed, out of
  strict order (step 23 still todo). Closes the long-deferred *live* verification
  of Immich's ML paths without fetching any ML model from the internet at test time.
    * **The bake** (`tests/fixtures/immich/ml-baked.Dockerfile`): `FROM
      immich-machine-learning:v3.0.3`, a build-time `snapshot_download` mirroring
      `immich_ml.models.base.ModelBase._download` (same `immich-app/<model>` repo,
      `/cache/<task>/<model>` layout, `*.armnn`/`*.rknn` ignore), CPU `.onnx` only
      (~0.77 GB), `.cache` dropped in-layer, `HF_HUB_OFFLINE=1`. `poe bake-immich-ml`
      also pre-pulls server/db/redis, so it is the one internet-touching step; test
      runs then find every image present and pull nothing.
    * **The test** (`tests/album/immich/test_immich_ml_integration.py`; opt-in
      `IMMICH_ML_INTEGRATION=1` / `poe test-immich-ml`; `@integration`, skipped in
      `poe check`): a real 4-service stack (server + baked ML + vectorchord Postgres
      + valkey) driven via testcontainers `DockerCompose`; uploads
      `skimage.data.astronaut()` (public-domain NASA face, no personal photo
      committed) through the real `ImmichClient` and asserts (a) a real
      machine-learning face via `get_faces` (closes step 17's live deferral), (b) the
      real `LeadFaceResolver` reference_image path uploads + polls real async
      detection to an ML-detected face (closes 17b timing), (c) smart search returns
      the asset — CLIP embedded offline (open Q1). New `ImmichClient.smart_search`.
    * Two adversarial Opus reviews (correctness + cleanup); all findings applied:
      harness moved from a hand-rolled `docker compose` subprocess to testcontainers
      `DockerCompose` (Ryuk cleanup + robust port resolution); pre-pull for a
      truly-offline run and corrected the "fully offline" overclaim; digest-pinned
      db/redis; `smart_search` added so the test drops private `_client` access; lead
      assertion tightened to require an ML-sourced face; `wait_jobs_idle` waits for
      the cascade to start before draining; the SQLite engine is guarded in a
      fixture; the bake command + version pin DRYed into the harness.
    * **Follow-up noted (not a blocker):** the baked image is ~3.1 GB, dominated by
      the base ML image (~2.3 GB; `/opt/venv` ~0.9 GB) — our models add only
      ~0.77 GB, so little to reclaim in our layer; a slim/multi-stage base is
      possible future work.
    * `uv run poe check` green (290 passed, 3 skipped); `uv run poe test-immich-ml`
      green (3 passed, real offline stack).

* **Enrichment step 20 done — rating + geocoded place; OCR deferred; enrichment
  playbook (17–20) complete** (commit `bb430d2`, branch
  `worktree-step-20-rating-place`; [enrichment.md](/build/enrichment.md) step 20).
  Two optional Immich signals, both read from data `sync` already fetches (no
  extra API call): the manual star `rating` and reverse-geocoded place
  (`city`/`state`/`country` off `exifInfo`). New nullable `Photo` columns
  (`rating`, `city`, `state`, `country`) + one migration (`88a302b1efdf`).
    * **`select`** gains a soft per-star `rating_boost` (default 0.02/star) folded
      into the existing effective score alongside the lead/group boosts — a starred
      photo edges out comparable frames, a rejected (negative) one is pushed below.
      It reorders *comparable* photos only (a full 5★ ≈ +0.10) and never overrides a
      clearly better shot; lead coverage is guaranteed separately, so rating never
      drops a lead. Inert for an unrated album (`rating` NULL/0 ⇒ byte-for-byte).
    * **`caption`** grounds on the photo's own place via a new `Location:` line
      (`PhotoAnalyzer._format_place` joins city/state/country most-to-least specific
      with dedup; `PhotoContext.photo_place`). `format_context` was refactored into
      per-line helpers (`_segment_line`/`_location_line`/`_people_line`/`_meta_line`)
      to stay under the CC≤5 gate. `cluster` was **deliberately not touched** — it
      keeps its GPS/DBSCAN scene logic; place is caption grounding only, not a
      clustering signal.
    * **OCR deferred** (the third step-20 signal): a separate `GET /assets/{id}/ocr`
      endpoint — a 2nd per-asset N+1 over the album — for what the playbook itself
      rates a "minor" signal, and absent from our pinned v3.0.2 API snapshot.
      Rating + place come free (fields on data already fetched); OCR does not. Full
      tradeoff write-up in enrichment.md step 20 (per user request), cross-referenced
      from immich-api.md/sync.md/data-model.md.
    * `sync._apply_location` copies gps + place onto the row, clearing stale values
      on a re-sync where exif disappears (same clear-on-update posture GPS had). The
      migration was autogenerated and verified: exactly the four columns, zero
      autogenerate drift, and a clean downgrade/upgrade round-trip on a real pgvector
      container.
    * Two adversarial Opus reviews (correctness: no defects; cleanup/conventions):
      findings applied — the `capturing_model` prompt-capturing double was promoted
      from two class-local copies to a single file-level fixture (a second consumer
      appeared), and `_format_place` switched to the `dict.fromkeys` dedup idiom used
      elsewhere. `uv run poe check` green — ruff, basedpyright, xenon (A), **290
      tests** (+13, all TDD red-first). Live-Immich re-verify of the rating/geocoding
      fields stays deferred (no reachable Immich, same boundary as steps 4-5). **This
      completes the Immich-enrichment playbook.**

* **Build step 28 done — document-driven segments (`--source-document`)**
  (commit `1e2b0af`, branch `step-28-segments-agent`; playbook step 3 / Feature B
  of [single-command-ingestion.md](/build/single-command-ingestion.md)). New
  `src/album/agents/segments.py`: a second PydanticAI agent (sibling to
  `analysis.py`) that turns a schedule document into the album's time/place
  `segments`. `extract_segments` is the pure agent call; `extract_segments_from_pdf`
  reads a PDF's text via `pypdf` and falls back to embedded page images for a
  scanned/image-only PDF. The output reuses the album's own `SegmentYAML` (plus an
  optional `title`); the agent emits ISO timestamps and the `album.yml` loader
  (`AlbumYAML`) stays the source of truth for correctness (UTC normalization +
  ordering/non-overlap).
    * **Wiring.** `photobook-generator generate --source-document=<pdf>` runs the
      agent inside `ingest` after the readiness poll, grounded on the photos'
      capture-date range (`_capture_range` over `get_album_assets`) and the album
      timezone, then writes the segments + a doc-suggested title into `album.yml`.
      Title precedence is now explicit `--title` > document title > directory name
      > slug. A loose extraction fails fast: `ingest` re-runs the real loader
      (`_validate_materialized`) and turns a rejection into a clear `IngestError`
      with fix-and-`--force` guidance, rather than crashing deep in the pipeline
      after reporting success (caught in review).
    * **Genericity held (the load-bearing constraint).** Every module/model/field/
      function name is occasion-agnostic; only the system-prompt *string* lists
      mixed example document kinds (itinerary/run-sheet/line-up/agenda), exactly as
      the plan prescribes to keep extraction generic — the cleanup reviewer
      confirmed PASS. The duplicated default-model factory was extracted to a shared
      `agents/_model.py` used by both agents.
    * Two adversarial Opus reviews (correctness + cleanup/genericity) — findings
      applied (fail-fast validation, shared model factory, `SegmentYAML.model_dump`,
      aware/naive capture-range normalization) — then a focused re-review of the
      fixes (clean). `uv run poe check` green (277 tests). Live-Claude extraction
      accuracy spot-check stays deferred (no `ANTHROPIC_API_KEY`), same pattern as
      steps 9/10/12; the scanned-PDF path is exercised via weasyprint-built
      fixtures round-tripped through pypdf. **Single-command-ingestion playbook
      (steps 26-28) complete.**

* **Build step 27 done — single-command `photobook-generator generate <dir>`**
  (commit `c1c5146`, branch `step-27-ingest-cli`; playbook step 2 / Feature A of
  [single-command-ingestion.md](/build/single-command-ingestion.md)). New
  `src/album/ingest.py`: `DirectoryIngestor` walks a photo directory (recursing;
  jpg/jpeg/png/heic/heif/webp), uploads each file via the existing
  content-deduped `upload_asset`, creates the Immich album (`create_album`, step
  26), polls `get_jobs` until EXIF extraction is idle (warn + continue on
  timeout — undated photos are handled downstream by `sync`/`cluster`), and
  materializes a minimal `data/albums/<slug>/album.yml`. It then calls the
  **unchanged** `pipeline(album_slug=...)` — `album.yml` stays the single source
  of truth and the hand-authored `photobook-generator run` path is untouched. Prefect-free and
  occasion-agnostic (genericity rule holds); shaped like `LeadFaceResolver`
  (injected Protocol client + poll/sleep).
    * **Two user-directed CLI decisions this step.** (1) Ingest is a *separate
      wrapper* in front of the pipeline (a sibling of `get_or_create_album`), not
      a `Stage` — a stage is built with an existing `album_id`/DB row, and ingest
      is precisely what creates the album.yml + Immich album that the row is
      bootstrapped from, so it must run before the stage machinery, and keeping it
      out front means one pipeline serves both entry modes. (2) **One** console
      entry point: the `album` script was renamed to **`photobook-generator`**
      with `generate <dir>` / `run --album <slug>` subcommands (replacing the two
      earlier separate scripts/functions). README/AGENTS/working-agreement/
      folder-structure + poe `run`/`generate` tasks updated.
    * **Re-run story (resolves an open question).** Load-once per slug: `generate`
      on an already-imported slug refuses unless `--force`, which re-runs the
      *existing* `album.yml` as is — no re-upload, no duplicate Immich album, and
      hand-authored `segments`/`lead_faces` preserved. The first-draft design
      (re-materialize + re-run) was caught in review as a data-loss + orphan-album
      footgun and redesigned. Image set = common phone/edited formats (RAW
      excluded); recurses subdirectories (both user-chosen).
    * Two adversarial Opus reviews (correctness + cleanup/reuse) — findings
      applied (flattened away speculative injected params, reused the albums-root
      constant for the CLI's book-path message, occasion-neutral docstring
      example) — then a focused correctness re-review of the `--force` redesign +
      rename (clean). `uv run poe check` green (264 tests). Live ingestion run
      stays deferred (same no-reachable-Immich boundary as steps 4-5/26).
      **Next: step 28** (`agents/segments.py` + `--source-document`, Feature B).

* **Build step 26 done — Immich album creation in the client** (commit `94fba06`,
  branch `step-26-immich-album-create`; playbook step 1 of
  [single-command-ingestion.md](/build/single-command-ingestion.md)). Adds two
  methods to `ImmichClient`: `create_album(name, asset_ids)` (`POST /albums`,
  returns the new album id — Immich creates the album and seeds its assets in one
  call) and `get_jobs()` (`GET /jobs`, returns `dict[str, ImmichJobCounts]`) — the
  readiness signal single-command ingestion (step 27) polls to wait out Immich's
  async EXIF/face/dedupe processing before running the pipeline. Boundary models
  stay lean per the `ImmichAsset`/`ImmichFace` pattern: `ImmichJobCounts` carries
  only the consumed `active`/`waiting` counts, with the per-queue `jobCounts`
  flattened up to the boundary (the single-field wrapper was dropped in review).
  Modelled against the Immich OpenAPI contract, not a live capture — same deferral
  as steps 4-5; re-verify on the first live ingestion run. Two adversarial Opus
  sub-agent reviews (correctness clean; cleanup/reuse findings applied) plus a
  focused correctness re-review after the fixes. `uv run poe check` green (241
  tests). **Next: step 27** (`ingest.py` + `photobook-generator` entry point —
  Feature A).

* **Plan added — [single-command ingestion](/build/single-command-ingestion.md)**
  (handover, not yet built). Captures two user-requested UX features and how they
  fit the current architecture: (1) `photobook-generator <dir>` — a one-shot that
  walks a local photo directory, uploads each file to Immich (reusing the existing
  content-deduped `ImmichClient.upload_asset`), creates an Immich album (new
  `create_album` client method), waits out Immich's async EXIF/face jobs (mirroring
  `LeadFaceResolver`'s poll), *materializes* a `data/albums/<slug>/album.yml`, and
  calls the existing `pipeline(album_slug=...)` unchanged; and (2)
  `--source-document=doc.pdf` — a new `agents/segments.py` PydanticAI agent (sibling
  to `analysis.py`; `pypdf` already a dep) that extracts the `segments:` block from a
  schedule/itinerary document, kept strictly occasion-agnostic per the
  folder-structure genericity rule. Recommends the app-feature path over a bare
  Claude skill (reproducible + testable via `TestModel`), with a thin
  `.claude/skills/` wrapper as optional dev-time sugar. Both are additive — no stage
  logic, data-model, or DAG changes; ~3 build steps. Indexed under Execution.

* **Refactor — a stage *is* its own task; `session_factory` in the constructor**
  (branch `refactor-stage-ctor-session`, user-directed). Reshapes the stage
  interface and every usage; behaviour-preserving (static gates green; 212 unit
  tests + the `test_flow_integration.py` real-Postgres concurrency test + the
  pgvector integration test all pass). Two adversarial Opus reviews
  (correctness; cleanup/tests/docs) — correctness found no defects; the doc/test
  findings were applied.
    * **New `TaskUnit` / `Stage` bases** (`flow/stages/base.py`). `TaskUnit[ResultT]`
      turns any unit into a named Prefect task from a class-level `task_name` and a
      no-argument `run`: `to_task()` = `task(name=task_name)(self.run)`,
      `run_as_task()` = `to_task()()`. `Stage[ResultT](TaskUnit)` is the Template
      Method for a stage.
    * **A stage is constructed with `(album_id, session_factory, *, force)` and holds
      no live `Session`.** `run()` opens the session from the factory, resolves +
      guards the album into `self._album`, runs the argument-free `_process`
      (reading `self._album`/`self._session`/`self._force`), then flushes + commits.
      Opening the session *inside* `run` is what makes the same instance safe to
      `.submit()` to a worker thread — a fan-out stage's session must be created on
      its own thread. This overturns the intermediate "`album_id` + live `Session`
      in the constructor" shape and the earlier session-per-call `run(album_id,
      session)`.
    * **`db/session.py` gains `reuse(session)`** — a `SessionFactory`
      (now `Callable[[], AbstractContextManager[Session]]`) that yields the shared
      session without closing it. The four sequential stages run inline via
      `run_as_task()` on `reuse(db_session)` (each commits the shared session before
      the next fan-out); the six fan-out units (incl. the `LeadFaceResolver`, now a
      sibling `TaskUnit`) run via `to_task().submit()` on the real pooled
      `session_factory`, opening/committing/closing their own session per worker
      thread.
    * **Deleted `flow/_tasks.py`** (the `autocommit`/`owns_session` wrappers) and
      **all module-level `*_task` functions** — `run()` owns the session lifecycle +
      commit, so there is no shared transaction wrapper or task glue left to hold.
      `flow/_flow.py` builds each stage and runs it via `run_as_task()` /
      `to_task().submit()`; the commit-before-fan-out and `expire_all()`-after-join
      invariants are unchanged.
    * **Docs updated on the branch:** `quality-practices.md` §1.2/§1.5/§1.6.1,
      `folder-structure.md` (tree + structure rules), `pipeline-execution.md`
      (§1/§2 + status note) and its `.html` mirror, `_flow.py` module docstring, and
      every stage module/class docstring. New `tests/album/db/test_session.py`
      asserts `reuse()`'s non-closing contract directly.

* **Refactor — co-locate each stage's `@task`; class-based `Pipeline` via factory**
  (branch `refactor-04-flow-tasks`). Four coupled changes to the flow layer,
  behaviour-preserving (all 237 tests green, incl. the `test_flow_integration.py`
  concurrency + enhance/caption-race guard).
    * **Each stage module now exports its own Prefect `@task`.** `flow/_flow.py`
      no longer defines the `@task` wrappers (`_sync_task`, …); each moved into its
      stage module as a public `sync_task`/`quality_task`/… co-located with the
      stage class it wraps. The task takes the **constructed stage instance** first
      (quality-practices.md §1.5), then `album_id` + session/`session_factory`, and
      calls `stage.run(...)`. `resolve_leads_task` additionally forwards the
      resolver's extra per-run inputs (parsed `lead_faces` config + its directory).
    * **Overturned invariant (user-directed).** The former rule *"`flow/stages/*.py`
      contain no Prefect imports; `flow/_flow.py` is the only Prefect-aware module"*
      is **reversed**: stages now import `prefect` to define their `@task`. The
      stage **class** stays Prefect-free and independently unit-testable without a
      Prefect runtime (tests still target `run`/`_process`, never the `@task`) —
      the testability the old rule protected is preserved. `flow/stages/base.py`
      still imports no Prefect, and nothing added Prefect to `db/`, `immich/`,
      `lulu/`, `printers/`, `agents/`, `config.py`, `definition.py`.
    * **New `flow/_tasks.py`** holds the two shared transaction-boundary wrappers
      (`autocommit` for the four sequential stages on the shared session;
      `owns_session` for the six fan-out stages on their own pooled session) — the
      session/commit *policy* stays an orchestration concern, not scattered across
      stage modules.
    * **`flow/_flow.py` is now a thin aggregator** built around a class-based
      `Pipeline` (holds the injected collaborators, constructs each stage, composes
      the DAG + session/client lifecycle). The public `@flow` is produced by the
      `Pipeline.flow()` factory: `pipeline = Pipeline.flow()`, keeping
      `pipeline(album_slug=…, **deps)` callable exactly as before (cli.py + tests
      unchanged in shape). `Pipeline` is re-exported from `album.flow`.
    * **Removed three redundant `db_session.commit()` calls** from the pipeline
      body — the ones right after `sync`/`select`/`assemble` that duplicated those
      tasks' own `autocommit` commit. The commit-before-fan-out invariant (each
      sequential task's own commit publishes state before the next fan-out) and the
      `expire_all()`-after-join invariant are both intact; the `expire_all()` calls
      were kept. The album-bootstrap commit (after `get_or_create_album`, which
      doesn't commit) was kept — it is not a task-duplicate.
    * **Docs updated on the branch:** `folder-structure.md` (tree + the reversed
      Prefect rule, with rationale, + the `Stage`-subclass rule) and
      `quality-practices.md` §1.5 (rewritten to the per-stage-`@task` +
      `flow/_tasks.py` + class-based `Pipeline` shape; See-also invariant note
      corrected). `base.py`'s docstring now points at the concrete stage module for
      the `@task`, not `_flow.py`.
* **Refactor — `src/album/flow/` pipeline package** (branch
  `refactor-01-flow-package`). Structural reorg so the only loose top-level
  modules under `src/album/` are `config.py`, `definition.py`, `cli.py`,
  `__init__.py`; everything pipeline-related now lives in a new `flow/` package.
  **Behaviour-preserving** — no logic changed, only file locations and import
  paths.
    * **Moves (via `git mv`, history preserved):** `flow.py → flow/_flow.py`;
      `tone.py → flow/tone.py`; `photo_analyzer.py → flow/photo_analyzer.py`;
      the whole `stages/` dir (`base.py`, `lead_faces.py`, the 9 stage files) →
      `flow/stages/`. New `flow/__init__.py` re-exports the public surface
      (`get_or_create_album`, `pipeline`) from `._flow`, so
      `from album.flow import pipeline` — which `cli.py` and the tests rely on —
      keeps resolving. The subpackages `db/`, `immich/`, `lulu/`, `printers/`,
      `agents/` did **not** move.
    * **Tests mirrored package-for-package:** `tests/album/stages/ →
      tests/album/flow/stages/`; `test_flow.py`, `test_flow_integration.py`,
      `test_tone.py`, `test_photo_analyzer.py` → `tests/album/flow/`.
    * **Imports rewritten** across `src/`, `tests/`, `alembic/`: `album.stages →
      album.flow.stages`, `album.tone → album.flow.tone`, `album.photo_analyzer →
      album.flow.photo_analyzer`. Stale-reference grep returns nothing.
    * **Docs updated on the branch:** `folder-structure.md` tree + structure
      rules (the "only module that imports Prefect" rule now names
      `flow/_flow.py`; tests tree gains `tests/album/flow/`), plus the now-wrong
      `flow.py`/`stages/` path references in `AGENTS.md`, `README.md`, and moved
      files' docstrings. The invariant "only `flow/_flow.py` imports Prefect"
      still holds. `pyproject.toml` needed no change (its globs use `src`/`tests`).
    * `uv run poe check` green (ruff + `ruff format --check` + basedpyright +
      xenon A-across-the-board + pytest, **233 tests** — same count as before, only
      import paths changed).
* **Refactor — nested pydantic-settings by domain** (branch `refactor-02-config`).
  `src/album/config.py`'s flat `immich_*` / `lulu_*` fields are now grouped into
  nested `ImmichSettings` / `LuluSettings` sub-models reached as `settings.immich.*`
  / `settings.lulu.*`, bound via the existing `env_nested_delimiter="__"`. Env vars
  renamed to match: `IMMICH_API_KEY → IMMICH__API_KEY`, `LULU_CLIENT_KEY →
  LULU__CLIENT_KEY`, `LULU_SHIPPING_* → LULU__SHIPPING_*`, etc. `database_url` and
  `anthropic_api_key` kept **flat** — single fields with de-facto-standard env names
  (`DATABASE_URL` consumed by alembic/compose; `ANTHROPIC_API_KEY` the SDK
  convention). Readers updated (`immich/client.py`, `lulu/client.py`,
  `printers/lulu.py`); `.env.example` + `README.md` renamed. `@cache get_settings()`
  and the "only module that reads env vars" contract preserved. `uv run poe check`
  green (**237 tests**, +4 `TestNestedGroups` binding tests).
* **Refactor — `definition.py` as a `Definition` ABC hierarchy** (branch
  `refactor-03-definition`). The module functions `load_album_definition` /
  `load_lead_faces` become a Template-Method ABC `Definition[T]` (`load` hoists the
  shared `yaml.safe_load` + `AlbumYAML.model_validate`; a `@staticmethod
  @abstractmethod _map(parsed) -> T` hook does the per-concern projection) with
  subclasses `AlbumDefinition(Definition[Album])` and
  `LeadFacesDefinition(Definition[LeadFacesYAML | None])` — mirroring the
  `flow/stages/base.Stage` idiom (quality-practices §1.6.1). Call shape
  `AlbumDefinition().load(path)`. Pydantic schema models
  (`AlbumYAML`/`SegmentYAML`/`LeadFacesYAML`) unchanged; behaviour-preserving
  (identical parsing / UTC normalization / validation errors). Callers updated
  (`flow/_flow.py` — import + 2 call sites; `tests/album/test_definition.py`); no
  back-compat wrappers left. `uv run poe check` green (**233 tests**).
* **Housekeeping — cat-album script + agent-worktree gitignore** (branch
  `chore-01-scripts-and-hygiene`). `scripts/build_cat_album.py` used the pre-class
  stage API (`cluster()`/`assemble()` functions) and pre-reorg `album.stages.*`
  import paths — broken since the Stage-class refactor (`880f512`) and the `flow/`
  move (`a6c3d15`), both of which left `scripts/` untouched (it is outside the
  ruff/basedpyright/pytest gate scope). Repointed to `ClusterStage().run(...)` /
  `AssembleStage(dir).run(...)` and `album.flow.stages.*` (`basedpyright
  scripts/build_cat_album.py`: 0 errors). `.gitignore` now excludes
  `.claude/worktrees/` so Claude Code agent worktrees stop being traversed by
  `ruff check .`.
* **Docs — knowledge-base sync for the flow/ + task refactors.**
  `foundations/pipeline-execution.md` (+ its `.html` companion) carried a
  "sketch, not applied" framing and `flow.py` module references; both are now
  corrected — a status banner records that the fan-out DAG is implemented (build
  step 25) and restructured (co-located per-stage `@task`s, `flow/_tasks.py`
  wrappers, class-based `Pipeline.flow()` factory), and `flow.py` → `flow/_flow.py`
  throughout. `reference/album-definition.md`'s `stages/lead_faces.py` path →
  `flow/stages/lead_faces.py`.

* **Refactor — `ToneParams` field mapping centralized** (branch
  `refactor-toneparams-field-mapping`, merged). The four bounded tone knobs
  (`exposure_ev`, `temp_shift_k`, `shadows`, `highlights`) were hand-copied
  field-by-field between `AnalysisResult` (bare names + caption), `ToneParams`,
  and the `enhance_`-prefixed `Photo` columns in **four** places; consolidated
  onto the value object.
    * **new `src/album/tone.py`:** `ToneParams` moved out of `stages/enhance.py`
      and given `from_analysis` (drops the caption), `from_photo` (coalesces an
      unset `enhance_*` column to `0.0`), and `write_to` (writes the four
      columns, never `caption`). The neutral leaf module breaks the `enhance →
      photo_analyzer` import cycle that had forced the copies to be duplicated
      (`ToneParams` lived in `enhance`, which `photo_analyzer` can't import back).
    * **`stages/enhance.py`:** the write-block becomes `combined.write_to(photo)`;
      the two `_ai_delta_from_*` helpers collapse into one `@staticmethod`.
      `reconcile_tone`/`_clamp`/`compute_camera_baseline` deliberately *stay*
      (shared `_clamp`, image deps; `reconcile_tone` is a §1.1 pure-transform).
    * **`photo_analyzer.py`:** `_persist` becomes
      `ToneParams.from_analysis(result).write_to(photo)` + the explicit caption
      write. `_is_already_analyzed`'s five-field presence guard is left as-is (a
      `None`-check, not a copy — and `from_photo` coalesces away the `None` it
      depends on).
    * Behaviour-preserving; `folder-structure.md` tree updated; the two files
      importing `ToneParams` from `enhance` now import from `album.tone`.
    * **Review — two adversarial sub-agents** (correctness: no defects; cleanup:
      4 findings applied — new `tests/album/test_tone.py`, doc/docstring trims)
      plus a re-review confirming resolution. `uv run poe check` green,
      **233 tests** (+7).

* **Enrichment step 19 — name-grounded `caption`** done (`cd77da6`, branch
  `step-19-name-caption`, merged). The last core [enrichment
  playbook](/build/enrichment.md) add-on for people: a selected photo's
  recognized *named* subjects now ground its caption, so it can say *"Alex and
  Sam at the harbour in Lisbon"* instead of *"two people at the harbour."*
    * **`agents/analysis.py`:** `PhotoContext` gains `person_names: list[str]`;
      `format_context` appends a `People in this photo: …` line **only when
      non-empty**; the system prompt now tells the model to use given names
      naturally and never invent one or say it doesn't recognize someone.
    * **`photo_analyzer.py`:** new pure `_resolve_person_names(photo)` maps the
      photo's `person_ids` to `Person.name`s via the album-scoped
      `photo.album.people` cache (**one query per album**, served from the
      session identity map — not an N+1), preserving `person_ids` order and
      dropping people unnamed in Immich (`Person.name is None`) or with no cached
      `Person` row. Wired into `ensure`'s `PhotoContext`, so the names reach the
      **one** shared vision call (no second round-trip) whether `enhance` or
      `caption` triggers it.
    * **Genericity guard:** a face-free album — or a photo whose people are all
      unnamed/unknown — resolves to `[]`, so no people line is emitted and the
      prompt is byte-for-byte what it was pre-step-19. No schema change.
    * **Tests (TDD, `TestModel`/SQLite, no API key):** 3 in `test_photo_analyzer.py`
      (named people reach the captured prompt via a prompt-capturing
      `FunctionModel`; unnamed/unknown ids dropped with no `None`/raw-id leak;
      face-free photo adds no line) + 2 in `test_analysis.py` (`format_context`
      with names / empty).
    * **Review — two *adversarial* sub-agents, per user's request** (one
      correctness-only, one style/readability/reuse-only): correctness found **no
      blockers/majors** (verified the None/`[]` collapse, both drop-cases, order,
      the one-query-per-album claim, and that the tests are true-green, not
      false-green). Style raised one should-fix + nice-to-haves, **all applied:**
      the prompt-capturing double moved from a module-level function to a
      `TestPeopleGrounding` class fixture (quality-practices.md §2.4); the canned
      `AnalysisResult` JSON — previously duplicated across `counting_analysis_model`
      and the new double — extracted into a shared `doubles.canned_analysis_response()`;
      the two classes' `_persist` aligned; a dead `str`-content branch and a
      defensive `getattr` dropped from the prompt walk; one assertion guarding a
      structurally-impossible case removed; the `PhotoContext.person_names` doc
      rationale de-duplicated against the resolver's docstring.
    * `uv run poe check` green, **223 tests** (+5). Live-Claude caption spot-check
      (build step 12 / enrichment 19) stays deferred — no `ANTHROPIC_API_KEY`,
      same standing deferral as steps 9/10/12. Also gitignored `.idea/` (a
      separate `chore:` commit, per user request). **Next enrichment step: 20
      (rating/OCR/geocoded place, optional).**

* **Build step 25 — parallelize the flow (fan-out groups)** done (branch
  `step-25-parallelize-flow`, merged, `036fcc2`). Applies the DAG execution
  documented in [`pipeline-execution.md`](/foundations/pipeline-execution.md):
  the four sequential stages (`sync`/`select`/`assemble`/`fulfill`) keep
  `flow.py`'s shared `Session`; the six grouped stages
  (`resolve-leads`/`quality`/`cluster`/`enhance`/`render`/`caption`) take a
  `session_factory` and open their own pooled connection via a new
  `_owns_session` decorator (+ a sibling `_autocommit` decorator for the
  sequential four, removing their repeated trailing `session.commit()`).
  Two `submit()`/`result()` fences: group 1
  (`resolve-leads`‖`quality`‖`cluster`, join at `select`) and group 2
  (`enhance`, then `render`‖`caption`, join at `assemble`).
  Commit-before-fanout / `expire_all()`-after-join at both fences, including
  before `assemble` (which reads group 2's `rendered_path`/`caption` writes
  — not spelled out in the original design sketch).
    * **Found + fixed a real bug (review, not the original design doc):**
      `enhance` and `caption` were originally both `.submit()`ed with no
      ordering between them — but both route through
      `PhotoAnalyzer.ensure`'s idempotency check, keyed on the same five DB
      columns. On separate per-task sessions with no ordering, a fresh run
      would have *both* fire a redundant vision-model call and race to
      commit conflicting values onto the same row (caption's raw AI delta
      vs. enhance's reconciled baseline+delta — whichever committed last
      would silently win). Three independent review passes converged on
      this. Fixed by joining `enhance`'s future explicitly before
      `render`/`caption` are even submitted — which also fixes a second,
      smaller issue: a real `enhance` failure now surfaces its own
      exception instead of a masked Prefect `UpstreamTaskError` from a
      `wait_for`-blocked downstream task.
    * New `tests/album/test_flow_integration.py`: the real `pipeline()`
      against a Postgres+pgvector testcontainer, genuine concurrent
      per-task sessions, every external boundary (Immich, vision model,
      darktable) doubled, including a `counting_analysis_model()`
      regression guard for the bug above (exactly one vision call per
      photo). `tests/album/test_flow.py` relaxed to partial-order
      assertions plus failure-isolation tests for both fences.
      `ConstantAesthetic`/`ConstantEmbedder` test doubles deduplicated into
      `tests/doubles.py`.
    * 218 tests green (`uv run poe check`); no `stages/*.py` gained a
      Prefect import. Live `photobook-generator run` end-to-end stays deferred (no
      reachable Immich/Claude/darktable/Lulu), same standing note as every
      prior step.

* **Immich keep-or-replace decision recorded** — new
  [`build/immich-dependency-decision.md`](/build/immich-dependency-decision.md) (linked from
  [`index.md`](/index.md)). Analyses what the pipeline actually consumes from Immich
  (two roles — managed library + faces/dedup ML — across seven API capabilities,
  all behind the [`sync`](/stages/sync.md) boundary), maps each to an open-source
  substitute (filesystem+exiftool+pyvips for storage; InsightFace `buffalo_l` +
  DBSCAN for the `Person` concept; pgvector/`imagehash` for dedup), and flags
  InsightFace's non-commercial model licensing. **Decision: keep Immich for now,
  preserve replaceability** via the `AssetSource`/`ThumbnailSource` protocols; build
  a `LocalPhotoSource` alongside and measure only when the second stack's cost bites
  or a headless deployment is needed. Complements (inverts) [open question
  #6](/build/open-questions.md) — we keep local ML *and* keep Immich, for now.

## 2026-07-15

* **TestContainers step 22 — darktable render integration** done (branch
  `step-22-darktable-render`, merged). Runs the **real** `DarktableRenderer`
  inside the **shipped app Docker image** (which carries `darktable-cli`) over a
  real source JPEG + a real `write_xmp_sidecar` sidecar, asserting the output
  JPEG exists, decodes, and is **visibly brighter** than the source
  (`tests/test_render_integration.py`, `@pytest.mark.integration`) — the
  automated form of build step 11's deferred "visibly corrected versus original"
  manual check, no internet model.
    * **Found + fixed a real bug (the point of the step):** `enhance` wrote XMP
      sidecars as *flat* attributes (`darktable:exposure="0.5"` …) — a schema
      `darktable-cli` **silently ignores**. Every render was a no-op copy; no
      tone correction ever reached an image (the exact failure build-11's manual
      check would have caught). Rewrote `write_xmp_sidecar` to darktable's real
      binary **history stack**: one `<rdf:li>` per non-zero knob with a
      hex-encoded C-params blob — `exposure_ev`→`exposure` (1:1 EV),
      `shadows`/`highlights`→`shadhi` (`[-1,1]`→`[-100,100]`),
      `temp_shift_k`→`temperature` (bounded Kelvin→R/B multiplier). Minimal
      backward-compatible scaffold (`xmp_version="4"`, no `iop_order_list` so
      darktable uses its own order); struct `modversion`s pinned to the app
      image's **darktable 5.0.1**. Verified module-by-module in a container
      (debug trace: "successfully loaded module exposure/shadhi/temperature").
    * **Investigation:** the flat schema was confirmed a 0-effect no-op (source
      luma == rendered luma == no-XMP-render luma) before the fix; a hand-built
      stack then produced +2 EV (luma 99→245). Ground-truth format taken from
      darktable's own reference sidecar (`src/tests/benchmark/darktable-bench-4.2
      .xmp`) + the `wmakeev/darkroom-xmp-tools` param encodings; struct layouts
      confirmed against `release-5.0.1` module sources.
    * **Landmine:** the test shells to the `docker` CLI, **not** docker-py — this
      sandbox's `~/.docker` credential helper (`docker-credential-gcloud`)
      crashes under Python 3.14 (`No module named 'imp'`), which breaks
      docker-py's/testcontainers' image *build*; the CLI resolves creds fine.
    * `test_enhance.py::TestXmpSidecar` rewritten to assert the history-stack
      structure + decode the exposure/shadhi params blobs (round-trip). Plain
      `uv run poe check` green (214 tests, 11 integration; Ryuk no longer needs
      disabling — landmine resolved this same day). No `src/` behavior change
      beyond `write_xmp_sidecar`; the `Renderer` protocol/orchestration is
      untouched.

* **Doc**: Added [Pipeline execution shape](/foundations/pipeline-execution.md) — documents
  that the pipeline runs as a linear chain today (one shared `Session`, committed
  per stage) but is a **DAG** underneath, with two parallelisable fan-out groups
  (`resolve-leads`‖`quality`‖`cluster` after `sync`; `enhance→render`‖`caption`
  after `select`), the critical path, per-stage responsibilities, and a sketch of
  the Prefect `submit()`/`result()` + own-session-per-task change to parallelise
  them (with the connection-pool / read-after-write / test caveats). Ships a
  self-contained `pipeline-execution.html` companion diagram. Not applied to
  `flow.py` — reference only.

* **TestContainers step 21 — pgvector integration suite** done (`ac2cd14`,
  branch `step-21-pgvector-integration`, merged). First step of the
  [TestContainers playbook](/build/testcontainers.md): promotes the DB
  boundary from in-memory SQLite + a numpy `StubSimilarity` to a **real
  Postgres+pgvector container migrated with alembic** (`upgrade head`, not
  `create_all`), joining what `test_select.py` (MMR logic) and
  `test_similarity.py` (the pgvector query) cover separately.
    * `tests/test_pgvector_integration.py`: (a) migrations reach head + the
      step-17/17b faces/people/lead columns exist + **no model↔migration drift**
      (`compare_metadata == []`, automating the by-hand autogenerate check each
      schema step); (b) `select` over the **real** cosine query — a near-dup is
      skipped by real pgvector distance (load-bearing `selected==2`), idempotent
      on re-run; lead coverage with a **negative control** (same near-dup pair
      dropped WITHOUT lead status, rescued WITH it) + a raw-SQL assertion that
      `person_ids` round-trips through Postgres JSON; (c) migration round-trip
      (upgrade → downgrade base → upgrade) on a throwaway container.
    * **Harness:** `_database_url` sets `DATABASE_URL` + clears the `get_settings`
      `@cache` and restores both (no cross-test leakage); the alembic `Config` is
      built **programmatically** so `env.py` skips `fileConfig` — which otherwise
      has `disable_existing_loggers=True` and silently broke `caplog` assertions
      in other test files that run after a migration (the one real bug caught in
      review). Infra: registered `integration` marker + `poe test-fast`
      (`-m "not integration"`) for a SQLite-only loop; shared `_vector`/image in
      `tests/pgvector_support.py`. Cluster integration deferred (its DBSCAN path
      is pure-Python lat/lon — no Postgres-specific behavior).
    * **Review:** 2 broad-scope sub-agents (fewer for a test-only diff) — harness
      correctness (found the fileConfig/caplog leak) + test quality (drove the
      negative control, JSON round-trip, idempotency re-run, drift check, shared
      helper). `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check` green, 211
      tests (10 integration).

* **Enrichment step 18 — people/lead-aware `select`** done (`f3afc54`, branch
  `step-18-people-select`, merged). The first consumer of step 17's
  `Photo.person_ids` and step 17b's `Person.is_lead`; layers people-awareness
  onto MMR and reduces to the old MMR **byte-for-byte** when a scene has no
  recognized faces.
    * **Effective score** = `aesthetic_score` + `group_boost`·people +
      `lead_boost`·num_leads (both small, tunable like `threshold`): a group shot
      or lead only wins a *close* aesthetic call; a frame with **more** leads (a
      whole couple) outranks one with fewer; boosts stay small so a distinct
      "original" scene is still picked on its own merits.
    * **Lead guaranteed coverage:** after the greedy pass every lead present in a
      scene gets a picked photo, bypassing the near-dup guard (a lead is never
      culled as a near-dup of a non-lead), skipping only exact Immich dup-group
      repeats; once per *uncovered* lead, so a couple sharing a near-dup frame are
      each covered. **Multiple leads per album** fully supported (per user Q).
    * **Strict on non-leads:** deliberately **no** generic-coverage phase — a
      non-lead seen only in near-dups stays unselected rather than padding the book
      (the user chose "strict — honor the guard"; the alternative "relaxed —
      everyone appears" was declined). Defaults: `lead_boost=0.05`,
      `group_boost=0.01` ("subtle tie-break" per user Q).
    * **User-driven refinements mid-build:** confirmed multiple leads per album;
      added "more leads wins among *similar* scenes, but an original scene with a
      lower lead score is still preferred" — realized as the *per-lead-count*
      boost (MMR already keeps every dissimilar/original photo, so lead count only
      breaks ties within near-dup clusters).
    * **Review:** 3 broad-scope sub-agents. Correctness clean. Cleanup proved a
      strict generic-coverage phase is **dead code** under the unbounded greedy
      (it re-applies phase-1's guards against a only-grown pick set, so it can
      never admit) — removed it and its now-inert helpers/parameter. Tests review
      drove: boost config-tunability tests (`lead_boost`/`group_boost` varied, not
      just accepted as kwargs), an explicit face-free genericity test (leads
      configured but no faces ⇒ plain-MMR result), and making the tie-break tests
      pass explicit boosts rather than lean on the module defaults.
      `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check` green, 205 tests (+11
      select cases). Docs: [stages/select](/stages/select.md),
      [stages/index](/stages/index.md).

* **Enrichment step 17b — lead-face resolution** done (`06810d7`, branch
  `step-17b-lead-faces`, merged). The user names the priority subjects (couple,
  baby, guest of honour) via `album.yml`'s `lead_faces`; the resolver turns that
  into a set of Immich person IDs and marks the matching `Person` rows
  `is_lead=True` for `select` (step 18). **No local face model** — Immich already
  recognizes library faces into people; the reference just *names* which are the
  leads (face embeddings aren't exposed over the API, same as CLIP — see
  [immich-api](/reference/immich-api.md)).
    * **definition:** `LeadFacesYAML` on `AlbumYAML` — **exactly one** of
      `reference_image` / `reference_asset_id` / `person_ids` — plus
      `load_lead_faces()`. Leads are a resolution *input*, not persisted `Album`
      state, so they're read from `album.yml` at resolution time, not stored.
    * **client:** `upload_asset()` (`POST /assets`, multipart) for the reference
      image. Immich content-dedups (`status="duplicate"` returns the existing
      id), so a repeat upload is safe.
    * **schema:** `Album.lead_reference_asset_id` (nullable) caches the uploaded
      reference's asset id so a re-run reuses it instead of re-uploading.
      Migration `d4a1c7b93e56`, verified on real Postgres (upgrade/downgrade,
      zero autogenerate drift).
    * **stages/lead_faces.py:** `LeadFaceResolver` (Prefect-free; injected client
      + `sleep`). `reference_image` uploads then **polls `get_faces` while
      Immich runs face detection asynchronously** (timeout → clear
      `LeadFaceResolutionError`); `reference_asset_id`/`person_ids` resolve with
      no upload; an unassigned reference face is **warned, not silently missed**.
      `_mark_leads` makes the album's lead set *exactly* the resolved set.
    * **flow:** `_resolve_leads_task` runs after `sync` (reads `lead_faces` from
      `album.yml`; a clean no-op when the file/block is absent). Genericity guard
      holds — no `lead_faces` ⇒ no `Person` rows touched.
    * **Review:** 3 broad-scope sub-agents. A correctness agent caught that
      `_mark_leads` only ever *set* `is_lead=True`, so editing `album.yml` and
      re-running would leave a removed lead marked forever — fixed to demote
      stale leads (the lead set now exactly matches the current config), with a
      test. Two test-strength gaps closed: the poll test now asserts the retry
      actually happened (`face_calls == [...]×3`), and a real run-then-rerun test
      pins single-upload idempotency (the prior test only pre-seeded the cache
      column). Cleanup flagged parallel structure with `sync`'s Person upsert and
      the flow reading `album.yml` — both kept as defensible (different mutation;
      resolver stays pure/testable with config injected).
      `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check` green, 194 tests.
    * **Docs updated alongside:** [album-definition](/reference/album-definition.md) (the
      `lead_faces` block), [data-model](/reference/data-model.md) (new column),
      [immich-api](/reference/immich-api.md) (`POST /assets` + async detection),
      [stages/index](/stages/index.md); a commented `lead_faces` example added to
      the shipped `smith-wedding/album.yml`.

* **Enrichment step 17 — faces/people in the client + `sync` (+schema)** done
  (`c99d319`, branch `step-17-faces-sync`, merged). First step of the
  [Immich enrichment playbook](/build/enrichment.md); adopts Immich's
  faces/people API (grown since the client was pinned at steps 4-5) for a
  people/coverage signal with **no local face-detection dependency**.
    * **client:** lean `ImmichFace` (a `model_validator(mode="before")` flattens
      the nested `person.id`/`.name` up to the boundary; `person: null` ⇒ a
      detected-but-unassigned face with no `person_id`) + `get_faces()` via
      `GET /faces?id=`, mirroring `get_duplicates`. `sourceType`
      (machine-learning|exif|manual) is carried per the playbook's model spec and
      test requirement, even though no stage consumes it yet.
    * **schema:** `Photo.face_count` (all detected faces) + `Photo.person_ids`
      (assigned Immich person UUIDs, JSON) — both nullable (NULL = not fetched,
      distinct from fetched-but-faceless `0`/`[]`) — plus an album-scoped
      `Person` cache table (`UniqueConstraint(album_id, immich_person_id)`;
      `is_lead` ships as a column, populated at step 17b). Migration
      `c3d9e5a1f742`; **verified against real Postgres** (upgrade + downgrade
      round-trip, and `alembic revision --autogenerate` produced an empty
      upgrade — zero model/migration drift).
    * **sync:** per-asset `get_faces` (the accepted N+1) populates the columns and
      upserts `Person` rows idempotently (keyed on `immich_person_id`; a rename
      propagates on re-sync; never clobbers a known name with NULL). The
      album-scoped `Person` cache is loaded once per run and mutated in place, so
      the same person on two assets doesn't double-insert.
    * **Failure isolation** matches the ambiguous-timestamp path: a face
      fetch/parse failing for one asset is logged + recorded in
      `SyncResult.face_errors`, leaving that photo's columns untouched rather than
      aborting the album. A correctness sub-agent caught that the first cut only
      caught `httpx.HTTPError`, so a malformed face payload
      (`pydantic.ValidationError`, raised inside `get_faces`) would have escaped
      and aborted the whole album — now both are isolated (two separate `except`
      clauses, per the py314 ruff landmine below).
    * **Genericity guard** holds: an album with no recognized faces lands
      `person_ids=[]`, and every downstream people-aware stage (built later) will
      reduce to its face-free behavior.
    * **Review:** 3 broad-scope sub-agents (correctness / cleanup /
      tests-conventions). Fixed: the ValidationError isolation gap; strengthened
      `sourceType` variant coverage (all three variants now asserted, incl. a
      `manual` fixture face); added a `person_ids` dedup test (`dict.fromkeys`).
      Cleanup flagged `source_type` as unused-but-carried — kept intentionally
      (playbook-specified). `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check`
      green, 177 tests.
    * **Docs updated alongside:** [data-model](/reference/data-model.md) (new columns +
      `Person`), [immich-api](/reference/immich-api.md) (faces surface; Open Q1
      re-confirmed — still no embeddings/asset-similarity endpoint),
      [stages/sync](/stages/sync.md), [stages/index](/stages/index.md).

## 2026-07-14

* **Decision: keep the local ML pipeline — do not offload to Immich or read its
  DB** (user-directed, resolves [open question #6](/build/open-questions.md)). Deep web
  research into what Immich computes and stores concluded we cannot drop
  `open_clip` (ViT-L/14) + the LAION aesthetic head:
    * **Immich has no aesthetic/quality score** — only a manual/Lightroom-imported
      star `rating`. Automated best-of-scene curation is entirely ours.
    * **Its CLIP embeddings are in an incompatible vector space.** Immich's default
      smart-search model is a **SigLIP** variant; the LAION aesthetic predictor is
      a linear head trained on **OpenAI CLIP ViT-L/14** (768-dim). SigLIP
      (sigmoid loss, WebLI corpus) ≠ OpenAI CLIP (softmax, LAION) even at equal
      dimension, so Immich's embeddings can't feed the predictor. Forcing Immich
      onto `ViT-L-14-336__openai` would degrade the user's *search* (SigLIP is
      better for it) — a bad coupling to impose.
    * **Therefore the CLIP backbone is unavoidable** (aesthetics needs it), and
      once it runs, `cluster`/`select` embeddings come free from the same pass —
      leaving nothing for Immich to offload.
    * **Reading Immich's Postgres directly was rejected.** Embeddings live in
      `smart_search.embedding` (`vector(N)`, N = configured-model-dependent) and
      face vectors in `face_search.embedding` (`vector(512)`, InsightFace
      `buffalo_l`), but the internal schema is an unversioned, churning contract:
      the vector extension migrated pgvecto.rs → pgvector/VectorChord, the
      embedding column width changes when the user switches CLIP model, and face
      embeddings moved `asset_faces` → `face_search` across releases (upgrade
      breakage in the wild). That violates the [architecture](/foundations/architecture.md)
      firewall against touching Immich's schema — too fragile to save a forward
      pass we keep anyway.
    * A **local-ML-free "minimal" variant** is technically possible (rank
      `select` on Immich star `rating` + faces + duplicate groups; temporal-only
      `cluster`; drop CLIP near-dup in favor of Immich duplicate groups) but
      trades away the core curation value — **not taken**. The faces/rating/OCR
      [enrichment playbook](/build/enrichment.md) is unaffected (additive,
      API-only). [Open question #1](/build/open-questions.md) (no embeddings on the API)
      stands, re-confirmed against the current API.
* **Review process: cap sub-agent review at ≤3 broad-scope agents**
  (user-directed, token-saving): `implementation-workflow.md` step 6 and the
  `AGENTS.md` loop summary now say to run a **maximum of 3 essential,
  broad-scope** review sub-agents (correctness / cleanup / tests-conventions)
  rather than fanning out into many narrow single-angle agents — fewer for a
  small diff, none for a trivial user-directed docs change; prefer low/medium
  `/code-review` effort over high/max unless the diff warrants the spend.
* **Printer abstraction + HTML default output** (post-step-16, user-directed):
  generalized `fulfill` from a Lulu-only submitter to a pluggable
  [`Printer`](/stages/fulfill.md), and made a browsable HTML book the default
  output medium.
    * New `src/album/printers/` package: `base.py` (`Printer` protocol,
      `BookPage`/`BookPhoto`/`PrintResult` types, and `iter_book_pages` — the
      shared reader that reconstructs the book's pages from the DB the way
      `assemble` paginated them, with the no-segments genericity fallback);
      `html.py` (`HtmlPrinter`, the **default** — renders a single
      self-contained `index.html` page-by-page viewer with captions, segment
      dividers, animated page turns and a progress bar, from Jinja macros +
      a CDN CSS lib + a little vanilla JS, referencing the on-disk rendered
      photos by relative path — no external service or credentials); `lulu.py`
      (`LuluPrinter`, the old `LuluPrintJobAdapter` moved out of the stage,
      now opt-in). `templates/book/{viewer.html,_components.html}` hold the
      viewer shell and its reusable presentational macros.
    * `Album.lulu_order_id` → `Album.output_ref` (medium-agnostic; migration
      `b7f2c1a4e8d0` renames the column). `stages/fulfill.py` now depends only
      on the `Printer` protocol (`FulfillResult.printed`/`output_ref`); each
      printer validates its own precondition, so the stage stays
      medium-agnostic. `flow.py`'s `print_job_submitter` param → `printer`,
      defaulting to `HtmlPrinter()` — no `LuluClient` is built or closed unless
      a `LuluPrinter` is injected. `album/lulu/client.py` (raw HTTP transport)
      is unchanged.
    * Tests: new `tests/test_html_printer.py` (page grouping, captions,
      segment dividers, no-segments fallback, on-disk relative `<img>` srcs,
      run-assemble-first guard) and `tests/test_lulu_printer.py` (moved adapter
      tests); `tests/test_fulfill.py` genericized against a fake `Printer`;
      `tests/test_flow.py` updated (default is `HtmlPrinter`, not Lulu). ruff +
      basedpyright + pytest green, **165 tests**, via
      `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check`. Self-reviewed with
      the `code-review` sub-agent.
* **Build step 16 done** (commit `d7e903d`, prove genericity): added
  `data/albums/smith-wedding/album.yml` — a real, shipped album definition
  structurally different from the trip-shaped `honeymoon-2026` that every
  prior stage's tests and the step-14 live run exercised: hour-scale segments
  ("Getting ready" / "Ceremony" / "Reception") instead of day-scale, a single
  day (`2026-08-15`, `Europe/London`) instead of several. Per build-order.md
  step 16 and domain-model.md, adding it required **only this YAML file** —
  confirmed by `git diff --stat main` showing zero files under `src/` touched.
  `tests/test_genericity.py` loads the real shipped file (not a copy under
  `tests/fixtures/`) through the same `get_or_create_album()` bootstrap
  `flow.pipeline()` uses, asserting the expected shape — a regression guard
  tying CI to the shipped data file, distinct from step 3's
  `tests/fixtures/albums/wedding.yml` unit test.
    * **Manual verification**, mirroring step 14: `docker compose up -d db
      prefect-server` + `uv run poe migrate` against real Postgres+pgvector
      (already at head, chain applies cleanly); `uv run photobook-generator run --album
      smith-wedding` against that real stack bootstrapped a real `Album` + 3
      `Segment` rows into Postgres (verified via `psql`: correct
      label/place/position, correct `Europe/London` → UTC conversion),
      started a real Prefect-tracked flow run (confirmed `FAILED` via the
      `/flow_runs/filter` API, same as step 14's `e2e-smoke-test` run), and
      failed cleanly with the same `httpx.ConnectError` boundary at `sync` —
      no reachable Immich instance in this environment, the same gap open
      since steps 4-5/14. GPS-tagged real photos aren't attachable without a
      live Immich instance; the GPS spatial-clustering code path itself is
      already unit-tested (step 7's "an album with GPS takes the spatial
      path").
    * **Self-review (code-review skill, high effort):** no findings — the
      diff is one data file plus one self-contained test against
      already-reviewed code (`get_or_create_album`, `load_album_definition`).
    * ruff + basedpyright + pytest green, 161 tests (160 pre-existing + 1
      new), run via `TESTCONTAINERS_RYUK_DISABLED=true uv run poe check`.
* **Build step 14 done** (commit `ada27d6`, wire the flow): added
  `src/album/flow.py` — the only module that imports Prefect. `pipeline()`
  wraps all 9 stages (sync, quality, cluster, select, enhance, render,
  caption, assemble, fulfill) as `@task`s around one shared,
  per-stage-committed `Session`; `get_or_create_album()` idempotently
  bootstraps an `Album` row from `album.yml` (reused on every later run,
  never re-imported). Every dependency is injectable, defaulting to the real
  thing from `config.py`, making `pipeline()` itself unit-testable
  (wiring/ordering) without touching any real external system.
  `src/album/cli.py`'s `run` subcommand now works, with a clean error
  message instead of a raw traceback for expected failures. `@runtime_checkable`
  added to five Protocol classes across `db/similarity.py`,
  `stages/quality.py`, `stages/render.py`, `stages/fulfill.py` — required
  because `pipeline()` (a Prefect `@flow`) takes them directly in its
  signature, and Prefect always builds a Pydantic parameter schema for a
  flow (even with `validate_parameters=False`, set here to stop that schema
  from rejecting test doubles).
    * **Manual verification:** brought up real Postgres (`pgvector/pgvector:pg16`)
      + a real Prefect server via `docker compose up -d db prefect-server`;
      `alembic upgrade head` applied the full migration chain (steps 2, 13,
      15) cleanly. `photobook-generator run --album e2e-smoke-test` against that stack
      bootstrapped a real `Album` row into Postgres, started a real
      Prefect-tracked flow run (confirmed via the API — state `FAILED`,
      exactly what the Prefect UI would show), and failed cleanly with
      `ConnectionRefused` at `sync` — the one boundary this environment
      can't cross (no reachable Immich instance with real photos, open
      since steps 4-5). Real `ANTHROPIC_API_KEY`, `darktable-cli` (Docker
      app image only), and Lulu credentials are also unavailable here.
      `docker-compose.yml` gained `PREFECT_API_URL` (mirroring the existing
      `DATABASE_URL` in-network override) and `prefect-server` in `app`'s
      `depends_on`; `README.md`'s run-locally section now has the real
      end-to-end command sequence.
    * **Self-review (8-angle high-effort code review) caught and fixed real
      issues:** `db_session`/`client` were constructed before the
      try/finally guarding them, so a failure building the Immich client
      after the session opened would leak the DB connection — restructured
      so client construction lives inside the guarded try. The `LuluClient`
      built for the default fulfill path was never closed (unlike the
      Immich client) — fixed, with a regression test. The `runtime_checkable`
      comments originally blamed `@task`'s "parameter-schema builder" for
      needing them; `@task` has no such machinery at all (verified against
      the installed Prefect source) — only `@flow` does; corrected across
      all four files. `_UnavailableAestheticScorer`/`_UnavailableEmbedder`
      were two near-identical classes for what's really one model — merged
      into `_UnavailableQualityModel`. `cli.py` had no error handling at
      all — added a clean-message-and-exit(1) path for expected,
      user-fixable errors.
    * **Deferred, not new to this step:** the real LAION/open_clip quality
      scorer (step 6's deferral, resurfaced here as `_UnavailableQualityModel`)
      and `fulfill`'s PDF-hosting gap (step 15) are both still open — see
      the progress tracker's notes.
    * ruff + basedpyright + pytest green, 160 tests (146 pre-existing + 5 in
      `tests/test_cli.py` + 9 in `tests/test_flow.py`).
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
      [Immich API notes](/reference/immich-api.md); also captured a partial answer to
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
  [`album.yml`](/reference/album-definition.md) via Pydantic (`AlbumYAML`/
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
  [data-model.md](/reference/data-model.md) exactly) and the first alembic migration,
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
* **Process**: Added the per-step [implementation workflow](/process/implementation-workflow.md)
  (branch per step → TDD → sub-agent review → user review → squash → fast-forward
  merge; never commit to `main`) and a living [progress tracker](/build/progress.md).
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

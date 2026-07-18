# Directory Update Log

Cross-commit narrative and decisions. Per-diff mechanics live in the commit
bodies this log cites; keep entries tight (see
[working-agreement · Be concise](/process/working-agreement.md#be-concise)).

## 2026-07-19

* **Step 34 — manifest fan-out + `generate --manifest`** (`0e67990`;
  [step-34-fanout-cli.md](/build/step-34-fanout-cli.md)). `ingest_manifest` ingests
  every album a manifest declares under ONE shared `ImmichClient`, returning an
  `AlbumOutcome` per album (slug on success, error on an isolated failure); the loop
  lives in `_ingest_all` so it unit-tests with a fake client, mirroring
  `ingest_directory`/`DirectoryIngestor`. `ingest.py` stays pipeline-free — it owns the
  ingest fan-out only; the CLI turns outcomes into pipeline runs. `generate --manifest`
  (opt-in; a stray `ingest.yml` never changes flat `generate`) rejects the per-album
  flags, threads the globals (`--timezone/--captioner/--force`), runs the pipeline once
  per ingested album with per-album isolation, and prints a combined books-written /
  albums-failed summary — exit non-zero only when *every* album failed (partial success
  is success). 4-agent review (2× correctness, style/reuse, architecture): no correctness
  defects; two applied fixes — the refute pass caught the captioner being resolved
  *outside* the manifest-mode error guard (a bad `CAPTIONER` env would crash with a raw
  traceback *after* GBs of uploads), so it now resolves up front inside the guard, failing
  fast; and the book-path print was DRY'd into a `_book_path` helper. **As-built trade-off
  upheld under review:** the per-album pipeline catch is deliberately narrow
  (`FileNotFoundError, ValueError`), so an *unexpected* exception aborts the batch before
  the summary prints — consistent with `_generate_single`'s "let real bugs surface"
  convention; broadening it is a future call if batch-completion is later valued over
  fail-fast. `poe check` green (404 passed, 6 skipped); live multi-album fan-out deferred
  to the standing no-reachable-Immich gap. Steps 32–34 (the feature's code) are now
  complete; only step 35 (user docs + e2e genericity gate) remains.

* **Step 33 — folder-derived segments in `ingest.py`** (`28c3ba0`;
  [step-33-folder-segments.md](/build/step-33-folder-segments.md)). New
  `DirectoryIngestor.ingest_album(spec, root)` ingests one manifest album, deriving
  each declared subfolder's `[start_at, end_at]` window from the capture timestamps of
  the photos in it. The upload → create → readiness → segments → materialize spine was
  factored into a shared `_ingest` behind a `SegmentSource` seam, so the PDF
  (`--source-document`) and folder paths differ only in *how* segments are computed; the
  flat/PDF `ingest()` path is behavior-preserving. Undated/empty folders are skipped with
  a warning (→ a zero-segment `album.yml`, byte-identical to the flat path), a
  single-instant folder widens `end_at` by 1s, and overlap is caught by the same
  `AlbumYAML` loader the pipeline uses (`_validate_materialized`, message generalized to
  be source-agnostic). **Design decision upheld under review:** the seam returns
  `(title_suggestion, segments)` rather than the plan's `list[SegmentYAML] | None` — the
  doc-title co-product *predates* this step (`_extract_segments` already returned it), so
  the tuple keeps the PDF's single-parse title+segments flowing through the shared spine
  without a second channel or spine special-casing; the architecture reviewer confirmed
  keeping it. 4-agent review (2× correctness, style/reuse, architecture): no correctness
  defects across all four; applied the style pass's DRY `_document_source` factory (one
  PDF source shared by both entry points) + fake-client cleanups. Two reviewers
  independently flagged a latent `path: "."` inconsistency (manifest `effective_slug`
  → `"album"` vs `ingest_album`'s dir-name slug), so the manifest now rejects `.` as an
  album/segment path. `poe check` green (389 passed, 6 skipped); live multi-folder ingest
  still deferred to the standing no-reachable-Immich gap.

* **Step 32 — ingest manifest schema + loader** (`445357f`;
  [step-32-manifest-schema.md](/build/step-32-manifest-schema.md)). New pure
  `src/album/manifest.py`: `Manifest`/`AlbumSpec`/`SegmentSpec` (`extra="forbid"`) +
  `load_manifest()`, parsing a root `ingest.yml` into validated album/segment boundary
  declarations. All shape/relationship validation, no filesystem or Immich I/O — the
  same posture as `AlbumDefinition` (validate shape) vs `ingest._collect_images` (touch
  disk), so path existence and image presence are deferred to steps 33/34. `SegmentSpec`
  is deliberately *not* `SegmentYAML`: it declares only which folder is a segment; the
  `[start_at, end_at]` window is derived from photo timestamps at ingest time. **One
  design choice beyond the plan:** `slugify`/`titleize` were hoisted out of `ingest.py`
  into a shared `src/album/text.py` (re-imported under the same private names) rather
  than importing `ingest._slugify` from `manifest.py` — that import would have become an
  `ingest ↔ manifest` cycle in step 33. 3-agent review (2× correctness, 1× style/reuse):
  both adversarial passes confirmed the sort-then-check-adjacent segment-nesting proof
  and full rule coverage, no defects; the style pass caught that the module's own
  docstrings/examples used the `trip`/`day` tokens the step-35 genericity guard must be
  able to grep-ban, so those were neutralized (`src/album/*.py` is now free of
  `trip`/`day`/`itinerary`, verified by grep). As-built tightening: `_is_unsafe_path`
  rejects *any* `..` component (stricter than the plan's "no escape after
  normalization", never less safe). `poe check` green (370 passed, 6 skipped); live
  multi-album ingest still deferred to the standing no-reachable-Immich gap.

## 2026-07-18

* **Plans — folder-structure-driven ingestion (steps 32–35)** (planning only, no code).
  New [folder-structure-ingestion.md](/build/folder-structure-ingestion.md) umbrella
  playbook + four step plans: a root `ingest.yml` **manifest** declares which subfolders
  are album vs segment boundaries, so `generate <root> --manifest` fans one tree into N
  independent Immich albums / `album.yml` files / pipeline runs / books. Folder-derived
  segments take their `[start_at, end_at]` window from the photos' capture timestamps.
  Six decisions settled with the user up front: root manifest (over marker files /
  interactive prompt); N independent runs (no pipeline change — every stage is already
  `album_id`-scoped); **explicit** `--manifest` opt-in (a stray `ingest.yml` never
  changes flat `generate`); per-album fields live in the manifest (CLI keeps only global
  `--timezone/--captioner/--force`); mixed content degrades cleanly to zero segments;
  per-album failure isolation with a combined summary; and build against today's upload
  path, staying decoupled from External Libraries. **Steps 32 → 33 → 34 → 35 are a strict
  sequential chain** (33 imports 32's models; 34 depends on 33's `ingest_album` seam and
  both edit `ingest.py`; 35 verifies) — no parallel build. Tracked in
  [progress.md](/build/progress.md) Outstanding.

* **Step 30 — repeatable `--lead-face` CLI flag + multi-photo lead resolution**
  (`e50a54a`; [step-30-cli-lead-faces.md](/build/step-30-cli-lead-faces.md)). `generate`/`run`
  take a repeatable `--lead-face IMAGE` that writes/patches the `lead_faces.reference_images`
  block, so priority subjects are named without hand-authoring `album.yml`. Two decisions
  changed the plan: (1) **verifying the plan first** showed dropping the scalar cache column
  orphaned the singular path → collapsed to **three symmetric list inputs**
  (`reference_images`/`reference_asset_ids`/`person_ids`; single = one-element, no singular
  forms); (2) **adversarial review** (2 correctness agents independently) caught the plan's
  *count-keyed* upload cache silently keeping the old lead on a same-count reference swap — the
  marquee replace path — so the DB cache was **dropped** for Immich's own content-dedup
  (re-upload each run; migration drops `Album.lead_reference_asset_id`). 3-agent review, all
  findings applied; `poe check` green (341/3); live multi-photo verify deferred.

* **Step 31 — real LAION/open_clip aesthetic scorer wired** (`2446901`;
  [step-31-laion-aesthetic-scorer.md](/build/step-31-laion-aesthetic-scorer.md)). New
  `album.analysis.aesthetic.ClipAestheticModel` (ViT-L/14 CLIP + LAION MLP head) backs
  both the `AestheticScorer` + `Embedder` protocols from one loaded model, with a
  one-entry feature memo so `score`+`embed` on a thumbnail forward once; torch/open_clip
  lazy-imported so the module (and `poe check`) stay torch-free. Delivered as an opt-in
  `scoring` extra (torch out of base deps) and capability-gated in `_flow._build_quality_model`:
  `quality.scorer="clip"` by default, but a missing extra/weights/bad-device-combo logs a
  loud warning and falls back to the unchanged NULL-scoring `_UnavailableQualityModel`
  (`select`'s tested fallback covers it) — so a base install still runs. No migration
  (`clip_embedding` already `Vector(768)`). **Two implementation-time deviations from the
  plan:** (a) quality deps resolve **lazily** in `_run_group_1`, not at `Pipeline`
  construction — building reads `get_settings()` (needs `DATABASE_URL`), which would break
  the resource-cleanup tests; matches the other lazy collaborators, keeps both-injected
  short-circuit torch-free; (b) `clip_model="ViT-L-14-quickgelu"` not plain `"ViT-L-14"` —
  open_clip flags a QuickGELU activation mismatch, and the OpenAI weights + the aesthetic
  head were both trained with QuickGELU. Offline monotonicity (sharp > blurry) + 768-dim +
  memo-hit verified via `poe test-scoring` (real weights, torch-guarded, out of `poe
  check`); live within-scene curation spot-check deferred (no reachable Immich).
  4-agent review (2× correctness, style/reuse, architecture) found no correctness bug;
  applied its hardening: validate `quality.scorer` (raise on an unknown value, like
  `build_captioner`, so a `none` typo can't silently enable the model) + `quality.device`
  (bogus device → one clean build-time fallback, not per-photo noise); dedup the weights
  path into `_weights_path`; `.part` cleanup on a failed head download. **Known dev-setup
  requirement (not a defect):** `poe check`'s basedpyright needs the `scoring` extra
  installed (`uv sync --extra scoring`) to resolve the lazy torch imports — uv 0.11.28 has
  no `[tool.uv] default-extras` to auto-install it, so a plain `uv sync` then `poe check`
  fails on `reportMissingImports`; documented in the playbook's Risks.

* **Step 31 follow-up — `poe check` made robust to a plain `uv sync`** (user-directed).
  Fixed the dev-setup wart above: a file-scoped `# pyright: reportMissingImports=false`
  in `analysis/aesthetic.py` (single-rule override for the one optional-torch module, not
  a blanket ignore) so the gate passes without the `scoring` extra. Chosen over a default
  dependency-group (which `--no-dev` wouldn't exclude → would bloat the production Docker
  image) — the pyright-side fix touches no install path. Verified both ways: torch absent
  (plain `uv sync`) → `poe check` green (353/6); torch present (`--extra scoring`) →
  basedpyright clean (override not flagged unnecessary) + `poe test-scoring` green, and the
  adapter is fully type-checked. CI should still `uv sync --extra scoring` for full coverage.

* **Convention added — commit body vs. `log.md` split + "Be concise" (user-directed).**
  [working-agreement.md](/process/working-agreement.md): a "Be concise" principle
  over all output, and a Git-workflow subsection dividing labour — commit bodies own
  the per-diff mechanical record, `log.md` owns the cross-commit narrative/decisions
  and points into git via SHAs rather than restating it. Decided **against** folding
  the log into commit history via a rewrite (entries don't map 1:1 to commits, bundle
  links wouldn't resolve, and a rewrite invalidates the SHAs the log cites).

* **README rewritten as a one-page brief + pipeline-execution destale (user-directed).**
  `README.md` → a brief (quick start + links into `knowledge/`). Fixed
  `pipeline-execution.md`/`.html`, which still framed the DAG fan-out as *proposed*
  (it shipped in step 25); corrected stage count (11 → 10).

* **Docs refactor + trim (user-directed; `e24bebd`).** Made top-level files thin
  pointers to the `knowledge/` bundle (source of truth); de-staled and de-duplicated.
  Deleted `HANDOVER.md` + `PROJECT_BRIEF.md`; added a 3-line `CLAUDE.md`. Assigned a
  canonical owner per repeated fact (TaskUnit → quality-practices; commit/fanout
  invariants → pipeline-execution; poe table → working-agreement), others cross-link.
  Net −1419 lines, markdown only.

## 2026-07-17

* **Step 29 — offline classical replacement for the Claude vision call** (`ba6c1bf`;
  [local-analysis.md](/build/local-analysis.md)). Split the fused per-photo vision
  call into two injectable collaborators in a new `album.analysis` package — a
  `ClassicalToneEstimator` (tone now always classical) and a `Captioner` seam
  (`TemplateCaptioner` offline default + opt-in `ClaudeCaptioner`), composed by
  `PhotoAnalyzer`. Selectable per run (`--captioner` / `CAPTIONER`). This removes the
  pipeline's **last external dependency** — a default run needs no API key. Accepted
  the `--force --captioner claude` ~2× cost on clustered photos. Live-Claude
  spot-check deferred.

* **Review panel widened to 4 (user-directed).** [implementation-workflow.md](/process/implementation-workflow.md)
  §6: up to 4 broad-scope review sub-agents — 2 adversarial correctness, 1
  style/reuse, 1 architecture — with "scale down for small diffs" (was a cap of 3).

* **Ruff trailing commas + `SimilaritySearch` → `Sequence` (user-directed).**
  Widened `db/similarity.py` read-only inputs to `Sequence` (`110c35a`). Enabled
  `COM812` + a repo-wide trailing-comma reformat (`ae8f4dd`), then moved `COM812` to
  `lint.ignore` (`de49b50`) after ruff warned it may conflict with the formatter —
  the reformat stays and the formatter's magic trailing comma keeps collections
  vertical, but trailing commas are no longer linter-enforced.

* **Open question 7 resolved — shared `selected_photos` query hoisted** (new
  `db/queries.py`; [open-questions.md](/build/open-questions.md) Q7). The
  "selected photos, ordered by id" read, copied across caption/enhance/render, is now
  one `selected_photos(album_id, session)` free function — chosen over a Repository
  (SQLAlchemy's Session already gives Unit of Work / Identity Map). Takes the caller's
  session, so it composes with fan-out session ownership. Fixed the latent drift Q7
  warned of — `caption` was missing `.order_by(Photo.id)` (determinism only).

* **Proposal recorded — offline analysis** (new [local-analysis.md](/build/local-analysis.md)).
  Design proposal to drop the last external-API dependency by splitting the Claude
  call into classical tone (CV) + metadata-template caption, keeping a VLM injectable
  as an optional "rich caption" mode. Not cost-driven (~$1–$4/album) — offline /
  no-key / deterministic. Implemented as step 29 above.

* **Open question 5 resolved — AI-pass cost estimate** (new
  [ai-cost-estimate.md](/build/ai-cost-estimate.md); Q5). The full Claude vision pass
  ≈ **$1–$4 per 300–500-photo album** (one idempotent call per selected photo).
  Verdict: acceptable, no cost control needed; caching not worth it (system prompt
  below Sonnet's cache floor). Metered re-check deferred to the first live run.

* **TestContainers step 24 — offline Immich ML integration** (`ad86550`;
  [testcontainers.md](/build/testcontainers.md)). Verifies Immich's ML paths (faces,
  smart search) against a real 4-service stack with **no model fetched from the
  internet at test time**: a build-time `snapshot_download` bakes CPU `.onnx` models
  into an image (`poe bake-immich-ml`, the one internet-touching step), driven via
  testcontainers `DockerCompose`. Uses `skimage.data.astronaut()` (public-domain, no
  personal photo). Closes step 17's live face-detection deferral. Opt-in
  (`IMMICH_ML_INTEGRATION=1`), skipped in `poe check`. Follow-up: baked image ~3.1 GB
  (dominated by the base image; little to reclaim in our layer).

* **Enrichment step 20 — rating + geocoded place; OCR deferred** (`bb430d2`;
  [enrichment.md](/build/enrichment.md)). Two optional Immich signals read from data
  `sync` already fetches (no extra call): manual star `rating` and reverse-geocoded
  place. New nullable `Photo` columns + one migration. `select` gains a soft per-star
  `rating_boost` (reorders comparable photos only; never drops a lead; inert for an
  unrated album). `caption` grounds on the photo's place. `cluster` deliberately
  untouched. **OCR deferred** — a 2nd per-asset N+1 for a "minor" signal, absent from
  the pinned v3.0.2 API. **Completes the Immich-enrichment playbook.**

* **Build step 28 — document-driven segments (`--source-document`)** (`1e2b0af`;
  [single-command-ingestion.md](/build/single-command-ingestion.md)). New
  `agents/segments.py`: a PydanticAI agent that turns a schedule PDF into the album's
  `segments` (text via `pypdf`, falling back to page images for a scanned PDF). Output
  reuses `SegmentYAML`; the `album.yml` loader stays the correctness authority (UTC
  normalization, ordering/non-overlap). Wired into `generate` after the readiness
  poll, grounded on the photos' capture-date range. A loose extraction **fails fast**
  with a clear `IngestError` (caught in review) rather than crashing deep in the
  pipeline. **Genericity held** — only the system-prompt *string* names example
  document kinds. Live-Claude accuracy spot-check deferred. **Completes the
  single-command-ingestion playbook (26–28).**

* **Build step 27 — single-command `photobook-generator generate <dir>`** (`c1c5146`).
  New `ingest.py`: `DirectoryIngestor` walks a photo dir, uploads via the existing
  content-deduped `upload_asset`, creates the Immich album, polls jobs until EXIF is
  idle (warn+continue on timeout), materializes a minimal `album.yml`, then calls the
  **unchanged** `pipeline()`.
    * **Two user decisions.** (1) Ingest is a wrapper *in front of* the pipeline, not
      a `Stage` — it creates the album row a stage would need. (2) One console entry
      point: `album` → `photobook-generator` with `generate`/`run` subcommands.
    * **Re-run story:** `generate` on an imported slug refuses without `--force`;
      `--force` re-runs the existing `album.yml` as-is (no re-upload, no duplicate
      album, hand-authored `segments`/`lead_faces` preserved). The first design
      (re-materialize + re-run) was caught in review as a data-loss footgun.

* **Build step 26 — Immich album creation in the client** (`94fba06`). Adds
  `create_album` (`POST /albums`) and `get_jobs` (`GET /jobs`, the readiness signal
  step 27 polls) to `ImmichClient`. Boundary models stay lean (per the
  `ImmichAsset`/`ImmichFace` pattern). Modelled against the OpenAPI contract, not a
  live capture — re-verify on the first live ingestion.

* **Plan added — [single-command ingestion](/build/single-command-ingestion.md)**
  (handover). Captures two user-requested UX features — `generate <dir>` and
  `--source-document=doc.pdf` — and how they fit the current architecture (additive:
  no stage logic, data-model, or DAG changes). Recommends the app-feature path over a
  bare Claude skill (reproducible + testable via `TestModel`).

* **Refactor — a stage *is* its own task; `session_factory` in the constructor**
  (user-directed; behaviour-preserving). New `TaskUnit`/`Stage` bases
  (`flow/stages/base.py`): a stage is constructed with `(album_id, session_factory,
  *, force)` and holds **no live `Session`** — `run()` opens the session from the
  factory inside the task, which is what makes the same instance safe to `.submit()`
  to a worker thread. Overturns the earlier "live `Session` in the constructor" shape.
  `db/session.py` gains `reuse(session)` (a non-closing factory for the inline
  sequential stages). Deleted `flow/_tasks.py` and all module-level `*_task`
  functions. Reviewed by 2 adversarial agents (correctness clean).

* **Refactor — co-locate each stage's `@task`; class-based `Pipeline`**
  (behaviour-preserving). Each stage module exports its own Prefect `@task` (moved out
  of `flow/_flow.py`). **Overturned invariant (user-directed):** the old "stages
  import no Prefect" rule is reversed — stages now import `prefect` to define their
  `@task`, but the stage **class** stays Prefect-free and unit-testable via
  `run`/`_process`. `flow/_flow.py` became a thin aggregator around a class-based
  `Pipeline` with a `Pipeline.flow()` factory. (Both this shape and `flow/_tasks.py`
  were superseded by the `TaskUnit` refactor above.)

* **Refactor — `src/album/flow/` package** (`refactor-01-flow-package`;
  behaviour-preserving, `git mv` history preserved). Moved all pipeline modules
  (`flow.py`, `tone.py`, `photo_analyzer.py`, `stages/`) under a new `flow/` package;
  `flow/__init__.py` re-exports the public surface so `from album.flow import
  pipeline` still resolves. `db/`, `immich/`, `lulu/`, `printers/`, `agents/` did not
  move.

* **Refactor — nested pydantic-settings by domain** (`refactor-02-config`). Flat
  `immich_*`/`lulu_*` fields → nested `settings.immich.*`/`settings.lulu.*` (env vars
  `IMMICH__*`, `LULU__*` via `env_nested_delimiter`). `database_url` and
  `anthropic_api_key` kept **flat** (de-facto-standard env names). "Only `config.py`
  reads env vars" preserved.

* **Refactor — `definition.py` as a `Definition` ABC** (`refactor-03-definition`;
  behaviour-preserving). `load_album_definition`/`load_lead_faces` → a Template-Method
  ABC `Definition[T]` (shared `yaml.safe_load` + validate; a `_map` hook per concern),
  mirroring `flow/stages/base.Stage`. Call shape `AlbumDefinition().load(path)`.

* **Housekeeping — cat-album script + agent-worktree gitignore**
  (`chore-01-scripts-and-hygiene`). Repointed `scripts/build_cat_album.py` to the
  Stage-class API + `album.flow.stages.*` (broken since the Stage refactor / `flow/`
  move; `scripts/` is outside the gate scope). `.gitignore` now excludes
  `.claude/worktrees/`.

* **Docs — knowledge-base sync for the flow/ + task refactors.**
  `pipeline-execution.md` (+ `.html`) de-staled — the fan-out DAG is implemented
  (step 25), not a sketch; `flow.py` → `flow/_flow.py` throughout.

* **Refactor — `ToneParams` field mapping centralized** (behaviour-preserving). The
  four bounded tone knobs were hand-copied field-by-field in **four** places;
  consolidated onto the value object in a new neutral `src/album/tone.py`
  (`from_analysis`/`from_photo`/`write_to`), which also breaks the `enhance →
  photo_analyzer` import cycle that had forced the duplication.

* **Enrichment step 19 — name-grounded `caption`** (`cd77da6`). A selected photo's
  recognized *named* subjects now ground its caption ("Alex and Sam at the harbour"
  vs "two people"). `PhotoContext` gains `person_names`; a pure
  `_resolve_person_names` maps `person_ids` → `Person.name` via the album-scoped
  people cache (**one query per album**, not an N+1), dropping unnamed/unknown ids.
  Reaches the one shared vision call. **Genericity guard:** a face-free album is
  byte-for-byte unchanged. Reviewed by 2 adversarial agents (correctness clean; style
  fixes applied). **Next: step 20.**

* **Build step 25 — parallelize the flow (fan-out groups)** (`036fcc2`). Applies the
  [`pipeline-execution.md`](/foundations/pipeline-execution.md) DAG: the four
  sequential stages keep the shared `Session`; the six grouped stages take a
  `session_factory` and open their own pooled connection. Two `submit()`/`result()`
  fences with commit-before-fanout / `expire_all()`-after-join at both.
    * **Real bug found in review (not in the design sketch):** `enhance` and `caption`
      both route through `PhotoAnalyzer.ensure`'s idempotency check keyed on the same
      columns. Submitted concurrently on separate sessions with no ordering, a fresh
      run would fire **two** redundant vision calls and race to commit conflicting
      values. Fixed by joining `enhance` before `render`/`caption` are submitted
      (also unmasks a real `enhance` failure instead of a Prefect `UpstreamTaskError`).
      New `test_flow_integration.py` guards exactly-one-vision-call-per-photo.

* **Immich keep-or-replace decision recorded** — new
  [`immich-dependency-decision.md`](/build/immich-dependency-decision.md). Maps what
  the pipeline consumes from Immich (managed library + faces/dedup ML, all behind the
  `sync` boundary) to open-source substitutes (filesystem+exiftool+pyvips;
  InsightFace+DBSCAN; pgvector/imagehash), flagging InsightFace's non-commercial
  licensing. **Decision: keep Immich, preserve replaceability** via the
  `AssetSource`/`ThumbnailSource` protocols; measure only when a second stack's cost
  bites or a headless deploy is needed.

## 2026-07-15

* **TestContainers step 22 — darktable render integration**
  (`step-22-darktable-render`). Runs the real `DarktableRenderer` in the shipped app
  image over a real JPEG + sidecar, asserting the output is visibly brighter — the
  automated form of step 11's deferred manual check.
    * **Real bug found (the point of the step):** `enhance` wrote XMP sidecars as
      *flat* attributes (`darktable:exposure="0.5"`), a schema `darktable-cli`
      **silently ignores** — every render was a no-op copy. Rewrote `write_xmp_sidecar`
      to darktable's binary **history stack** (one `<rdf:li>` per non-zero knob, a
      hex-encoded C-params blob), verified module-by-module in a container (+2 EV: luma
      99→245). Struct `modversion`s pinned to the app image's darktable 5.0.1.
    * **Landmine:** the test shells to the `docker` CLI, **not** docker-py — this
      sandbox's `docker-credential-gcloud` helper crashes under Python 3.14
      (`No module named 'imp'`), breaking docker-py's image build; the CLI resolves
      creds fine.

* **Doc — [Pipeline execution shape](/foundations/pipeline-execution.md).** Documents
  the stage DAG (two parallelisable fan-out groups), the critical path, and a sketch
  of the Prefect own-session-per-task change to parallelise it (with pool /
  read-after-write / test caveats). Ships a self-contained `.html` diagram.
  (Reference only then; implemented as step 25.)

* **TestContainers step 21 — pgvector integration suite** (`ac2cd14`). First step of
  the [playbook](/build/testcontainers.md): promotes the DB boundary from in-memory
  SQLite + a numpy stub to a **real Postgres+pgvector migrated with alembic**. Covers
  migrations-reach-head + **no model↔migration drift** (`compare_metadata == []`,
  automating the by-hand autogenerate check), `select` over the real cosine query
  (near-dup skipped by real distance; lead coverage with a negative control), and a
  migration upgrade→downgrade→upgrade round-trip.
    * **Landmine found in review:** the alembic `Config` must be built
      **programmatically** so `env.py` skips `fileConfig`, whose
      `disable_existing_loggers=True` silently broke `caplog` assertions in test files
      running after a migration. Registered the `integration` marker + `poe test-fast`.

* **Enrichment step 18 — people/lead-aware `select`** (`f3afc54`). First consumer of
  step 17's `person_ids` and 17b's `is_lead`; reduces to old MMR **byte-for-byte** for
  a face-free scene.
    * **Effective score** = aesthetic + `group_boost`·people + `lead_boost`·num_leads
      (small, tunable). **Lead guaranteed coverage:** after the greedy pass every lead
      in a scene gets a picked photo (bypassing the near-dup guard, once per uncovered
      lead) — multiple leads per album supported.
    * **Strict on non-leads (user choice):** no generic-coverage phase — a non-lead
      seen only in near-dups stays unselected rather than padding the book. Cleanup
      proved a strict generic-coverage phase would be dead code under the unbounded
      greedy — removed. Defaults `lead_boost=0.05`, `group_boost=0.01`.

* **Enrichment step 17b — lead-face resolution** (`06810d7`). The user names priority
  subjects via `album.yml`'s `lead_faces`; the resolver turns that into Immich person
  IDs and marks matching `Person` rows `is_lead`. **No local face model** — Immich
  already recognizes faces; the reference just *names* the leads.
    * `LeadFacesYAML` requires **exactly one** of
      `reference_image`/`reference_asset_id`/`person_ids`. Leads are a resolution
      *input*, read at resolution time, not persisted `Album` state.
    * `reference_image` uploads via `upload_asset` (Immich content-dedups) then
      **polls `get_faces` while detection runs async** (timeout → clear error);
      `Album.lead_reference_asset_id` caches the upload so a re-run reuses it.
    * **Review caught:** `_mark_leads` only ever set `is_lead=True`, so removing a lead
      from `album.yml` left it marked forever — fixed to make the lead set *exactly*
      the current config.

* **Enrichment step 17 — faces/people in the client + `sync` (+schema)** (`c99d319`).
  First step of the [enrichment playbook](/build/enrichment.md); adopts Immich's
  faces/people API with **no local face-detection dependency**.
    * `ImmichFace` flattens the nested `person` up to the boundary; `get_faces` via
      `GET /faces?id=`. New `Photo.face_count`/`person_ids` (nullable: NULL = not
      fetched, distinct from `[]`) + an album-scoped `Person` cache table.
    * `sync` per-asset `get_faces` (accepted N+1) upserts `Person` idempotently (a
      rename propagates; never clobbers a name with NULL). **Failure isolation:** a
      face fetch/parse error is recorded per-asset, not aborting the album — review
      caught that the first cut only caught `httpx.HTTPError`, so a malformed payload
      (`ValidationError`) would have escaped.

## 2026-07-14

* **Decision: keep the local ML pipeline — do not offload to Immich or read its DB**
  (user-directed; resolves [open question #6](/build/open-questions.md)). Web research
  into what Immich stores concluded we can't drop `open_clip` (ViT-L/14) + the LAION
  aesthetic head:
    * Immich has **no aesthetic/quality score** (only a manual star rating) — best-of
      curation is entirely ours.
    * Its CLIP embeddings are an **incompatible vector space** — Immich's default is a
      **SigLIP** variant; the LAION predictor is a linear head on **OpenAI CLIP
      ViT-L/14**. Forcing Immich onto OpenAI CLIP would degrade the user's *search*.
    * So the CLIP backbone is unavoidable; once it runs, `cluster`/`select` embeddings
      come free — nothing left to offload.
    * **Reading Immich's Postgres directly rejected** — the internal schema is an
      unversioned, churning contract (vector extension + column widths + table layouts
      have all moved across releases); violates the architecture firewall.
    * A local-ML-free "minimal" variant is possible but trades away the core curation
      value — **not taken**. [Open question #1](/build/open-questions.md) (no
      embeddings on the API) re-confirmed.

* **Review process: cap sub-agent review at ≤3 broad-scope agents** (user-directed,
  token-saving). `implementation-workflow.md` §6 + `AGENTS.md`: a **maximum of 3**
  essential broad-scope agents (correctness/cleanup/tests), fewer for a small diff.
  (Later widened to 4 — see 2026-07-17.)

* **Printer abstraction + HTML default output** (post-step-16, user-directed).
  Generalized `fulfill` from a Lulu-only submitter to a pluggable
  [`Printer`](/stages/fulfill.md); a browsable self-contained HTML book is now the
  **default** output (no external service/credentials). New `printers/` package
  (`base.py` protocol + shared `iter_book_pages`; `html.py` default; `lulu.py`
  opt-in). `Album.lulu_order_id` → `Album.output_ref` (medium-agnostic).

* **Build step 16 — prove genericity** (`d7e903d`). Added
  `data/albums/smith-wedding/album.yml` — a real shipped album structurally different
  from the trip-shaped `honeymoon-2026` (hour-scale segments, single day). Per the
  domain model, adding it required **only this YAML** (confirmed by `git diff --stat`:
  zero `src/` files). `test_genericity.py` loads the real shipped file through the same
  bootstrap the pipeline uses.
    * **Manual verify:** `run --album smith-wedding` against real Postgres+Prefect
      bootstrapped the album + segments correctly and failed cleanly at `sync` — no
      reachable Immich (the standing sandbox gap since steps 4-5).

* **Build step 14 — wire the flow** (`ada27d6`). Added `src/album/flow.py` (the only
  Prefect module): `pipeline()` wraps all 9 stages around one shared,
  per-stage-committed `Session`; `get_or_create_album()` idempotently bootstraps an
  `Album` from `album.yml`. Every dependency is injectable, so `pipeline()` is
  unit-testable without touching real externals. `@runtime_checkable` added to five
  Protocols (Prefect builds a Pydantic parameter schema for a `@flow`).
    * **Self-review fixed real issues:** a DB connection leak if the Immich client
      failed to build after the session opened; an unclosed default `LuluClient`; a
      `@task`-vs-`@flow` comment error.
    * **Deferred:** the real LAION/open_clip scorer (step 6) and `fulfill`'s
      PDF-hosting gap (step 15); live run blocked at `sync` (no reachable Immich).

* **Build step 11 — `render` stage** (`168940f`). `darktable-cli` batch over the XMP
  sidecars `enhance` writes; added `ImmichClient.get_original()` (a real gap —
  darktable needs source pixels, XMP carries only edit instructions). A selected photo
  with no sidecar yet is counted `not_ready`, not dropped. Self-review fixed: no
  output-file check; the staged original re-fetched on every retry and never cleaned
  up. Deferred: the manual "visibly corrected JPEG" check (no `darktable-cli` on host)
  — closed later by step 22.

* **Build step 12 — `caption` stage** (`69f82b3`). A thin stage that iterates
  `selected` photos and reuses step 10's shared idempotent vision-call trigger.
  Order-independent with `enhance` — whichever runs first triggers the one shared call
  per photo. Deferred: the manual ~20-caption accuracy spot-check (no API key).

* **Parallel build: steps 10/13/15 built concurrently** (user-directed, separate
  worktrees — they share no files; 11 and 12 genuinely depend on 10, so weren't
  parallelized). A coordinator merged all three serially, reconciling the one real
  conflict — `assemble` and `fulfill` each added an `Album` column, so `fulfill`'s
  placeholder `assembled_pdf_path` was dropped for `assemble`'s real one.

* **Build step 10 — `enhance` stage** (`e7fccfe`). `ensure_analysis()` — the shared
  idempotent "one combined vision call" trigger `caption` reuses; cross-camera
  exposure baseline via `match_histograms` per scene, plus the AI's per-photo deltas;
  writes darktable-native XMP sidecars. Self-review fixed: a force-re-run bug that
  compounded the baseline each time; unclustered photos vanishing silently; a
  `camera_model` None/None false-match.

* **Build step 13 — `assemble` stage** (`9ec5ca7`). Groups `selected` photos by
  segment/scene/time, cycles 1up/2up/3up layouts, renders via WeasyPrint to
  `assembled.pdf`.
    * **Resolved Open Q4 (Lulu print spec, fetched 2026-07-14):** 8.5×8.5in trim,
      0.125in bleed, spine = page_count/444 + 0.06in (softcover perfect-bound).
    * Self-review fixed: `<img>` paths resolved against the templates dir not cwd; the
      zero-photo fallback PDF defaulted to A4 instead of the Lulu page size; a missing
      `rendered_path` produced `<img src="None">` instead of raising.

* **Build step 15 — `fulfill` stage** (`92a7d4c`). `LuluClient` (OAuth2
  client-credentials; `POST /print-jobs/`) + `LuluPrintJobAdapter` (keeps fulfillment
  policy out of the thin HTTP client). Researched against Lulu's public OpenAPI (the
  `GET /print-jobs/{id}/` schema was inferred, not confirmed).
    * **Flagged gap:** no PDF-hosting/upload mechanism yet, so a real submission fails
      loudly (`UnreachablePdfError`) rather than sending Lulu an unfetchable local
      path — follow-up for the first live order. Test-order verify deferred (no Lulu
      sandbox creds).

* **Closed step 9's deferred Docker/testcontainer re-verification.** Docker Desktop
  reinstalled, fixing the flakiness noted below; full `poe check` passes clean with no
  code changes.

## 2026-07-13

* **Build step 9 — agent `analysis.py`** (`38a4057`). The single combined PydanticAI
  vision agent: one request per selected photo yields both bounded tone deltas
  (`Field(ge=, le=)`) and a caption in one `AnalysisResult`. Deliberately
  DB-free/side-effect-free (persistence + idempotency left to the calling stages),
  mirroring `immich/client.py` vs `sync.py`. Built without a bound model so importing
  never requires an API key.
    * **Review caught a real leak:** `_default_model()` built a fresh
      `AnthropicProvider` (+ its own connection pool) on every call, never closed —
      fixed by `@cache` (same pattern as `get_engine()`). Also: an empty caption passed
      validation (no `min_length`).
    * **Deferred:** the "wire real Claude + sanity-check ~20 photos" manual step (no
      API key). **Landmine:** Docker Desktop went fully unresponsive mid-step, so the
      pgvector testcontainer test couldn't be re-verified here (closed same-day above).

* **Build step 8 — `select` stage** (`4fec4a1`). Diversity-aware best-of-scene via MMR:
  within each scene, rank by `aesthetic_score`, greedily pick, skipping Immich
  duplicates and any candidate whose CLIP cosine similarity to a picked photo exceeds
  0.9. The pgvector query sits behind a `SimilaritySearch` protocol so MMR logic is
  unit-tested on SQLite, the real query against a **Postgres testcontainer** (first use
  of `testcontainers-python`).
    * **Review caught a real crash:** `clip_embedding` is nullable by design but
      `select()` called the similarity query unconditionally — a missing embedding
      raised `ValueError`/`TypeError` (pgvector `NULL` → `min()` choke), crashing the
      whole album. Fixed: pass the candidate's in-memory embedding directly and filter
      NULLs; both paths now return `-1.0` instead of raising.
    * **Landmine:** in this sandbox `testcontainers` Ryuk can't start (socket mount
      denied) — set `TESTCONTAINERS_RYUK_DISABLED=true`; `PostgresContainer` defaults
      to a psycopg2 URL, so pass `driver="psycopg"` (this project is psycopg3).

* **Build step 7 — `cluster` stage** (`2a39df5`). Assigns each photo a `segment_id`
  (UTC timestamp vs the album's ordered segments) and a `scene_id`. Scene detection is
  **temporal** (>~90 min gap) by default, **spatial** (DBSCAN over lat/lon, haversine)
  when every photo in a group has GPS. **Genericity guard, tested:** no segments →
  one implicit segment; no GPS → pure time-gap scenes. Out-of-range / undated photos
  are flagged, not dropped. Review applied: `.order_by(Photo.id)` for determinism on
  Postgres; a documented inclusive segment-boundary convention.

* **Build step 6 — `quality` stage** (`a6c4b9e`). Scores blur + aesthetics per photo.
  **Blur** is real (Laplacian variance over the thumbnail); **aesthetic** and **CLIP
  embedding** are injected behind `AestheticScorer`/`Embedder` protocols (the
  open_clip/LAION/torch stack is heavy), keeping the stage unit-testable. Idempotent,
  per-photo failure isolation. **Deferred:** the real aesthetic/embedding models,
  wired via injection, supplied when the flow lands (step 14). Review caught a partial
  write — a mid-photo failure left a stray `blur_score` the trailing flush persisted;
  fixed to assign to the `Photo` only once all computation succeeds.

* **Build steps 4 + 5 — `sync` stage + Immich client** (delivered together per user).
  `immich/client.py` — a thin typed `httpx` wrapper (album-asset listing, EXIF,
  duplicate groups, thumbnails) — and `sync.py`, the idempotent Immich→DB boundary
  (upserts `Photo` keyed on asset id, normalizing capture time to UTC:
  aware→zone→album→ambiguous-NULL).
    * **Open question #1 resolved:** the Immich REST API (v3.0.2) exposes **no CLIP
      embeddings** — `quality` computes its own. Recorded in
      [immich-api.md](/reference/immich-api.md).
    * **Live verification skipped** (per user): Immich unreachable, so fixtures derive
      from the OpenAPI contract; re-verify on the first real sync.
    * **Landmine (ruff 0.15.21 formatter bug):** under `target-version = "py314"`,
      `ruff format` rewrites a valid `except (A, B):` into the invalid paren-less form
      — a catch-22 with Python/basedpyright. **Avoid parenthesized multi-type `except`
      clauses** (use separate clauses) until fixed.

* **Build step 3 — `config.py` + `definition.py`** (`84260ef`). `config.py`, the first
  and only env-reading module (pydantic-settings, `env_nested_delimiter` set up front
  so later grouped settings don't touch it again); `definition.py` parses `album.yml`
  into unpersisted ORM rows, normalizing segment times to UTC. `alembic/env.py` now
  reads `DATABASE_URL` via `config.py`. Review fixed two DST/timezone bugs: an unknown
  IANA name leaked a raw error instead of `ValidationError`; a naive time in a
  spring-forward gap was silently converted instead of raising.

* **Build step 2 — ORM models + first migration** (`68ed96f`). `db/models.py`
  (`Album`/`Segment`/`Photo`, mirroring [data-model.md](/reference/data-model.md)); the
  first migration runs `CREATE EXTENSION IF NOT EXISTS vector` before the `pgvector`
  column. Verified upgrade/downgrade/upgrade against real Postgres. Review fixed: `poe
  migrate` never loaded `.env` outside Docker, so a custom `DATABASE_URL` silently fell
  back to the placeholder — fixed via poethepoet's `envfile`.

* **Build step 1 — Docker + compose** (`240fbe0`). Multi-stage `Dockerfile` (uv sync
  → runtime with `darktable-cli`) + `docker-compose.yml` (our app + our own pgvector
  Postgres + Prefect 3).
    * **Decision (user):** Immich's stack is vendored into its own
      `docker-compose.immich.yml` (close to Immich's official release compose) and
      pulled in via Compose `include:`, so a new Immich release is near a drop-in swap.
    * Review: `db` host port overridable via `APP_DB_HOST_PORT`; `poe up` passes
      `--build`.

## 2026-07-12

* **Creation.** Transformed `PROJECT_BRIEF.md` into this OKF v0.1 knowledge bundle —
  per-concept documents + a `stages/` subdirectory (one doc per stage).
* **Decision:** adopted **basedpyright** as the type checker, with Astral **`ty`** as
  the planned future migration once stable.
* **Decision:** encoded the workflow as **poethepoet** tasks (`uv run poe <task>`);
  required `README.md` + `AGENTS.md` at scaffold.
* **Decision:** resolved the `enhance`/`caption` contradiction — a **single combined
  vision agent** produces tone deltas *and* caption in one idempotent call; both
  stages consume its output (one round-trip per selected photo). (Later replaced by
  the offline analysis of step 29.)
* **Decision:** all datetimes stored tz-aware in **UTC**; `album.yml` carries offsets
  or an album `timezone`; `definition.py`/`sync` normalize. Narrowed to determining a
  photo's source zone from EXIF (Open Question #3).
* **Process:** added the per-step [implementation workflow](/process/implementation-workflow.md)
  and a living [progress tracker](/build/progress.md); never commit to `main`.
* **Decision:** local `pre-commit` hooks at scaffold (`no-commit-to-branch`, ruff,
  basedpyright), full pytest left to `poe check` + CI.
* **Build step 0 — scaffold** (`11d290f`). `uv` project (Python 3.14), ruff/basedpyright/
  pytest + poe tasks, the package skeleton (`config.py`/`definition.py`/`flow.py`
  deferred to steps 3/14), `.pre-commit-config.yaml`. Verified a direct commit to
  `main` is rejected by the hook.

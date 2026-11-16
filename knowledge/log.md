# Directory Update Log

Cross-commit narrative and decisions. Per-diff mechanics live in the commit
bodies this log cites; keep entries tight (see
[working-agreement · Be concise](process/working-agreement.md#be-concise)).

## 2026-07-21

* **PDF rendered on demand in `LuluPrinter`, not on every run (step 43,
  user-requested; commit `9663d98`).** The user asked why the print-ready PDF
  was generated on every pipeline run when the default output is a browsable
  webpage. Root cause: `assemble` unconditionally rendered the master PDF via
  WeasyPrint, but only an opt-in Lulu order reads it — the default `HtmlPrinter`
  renders from the on-disk photos and never touches it. **Change:** `assemble` is
  now layout-only — it stamps each placed photo's `page_number` (the signal
  `printers/base.py:iter_book_pages` reads) and computes the Lulu spine width,
  rendering no file; its idempotency now keys off "photos already placed" rather
  than a written `assembled_pdf_path`. `LuluPrinter` renders the PDF on demand
  from `iter_book_pages` (rebuilding assemble's section pages from
  `starts_segment`, so the page count still matches the spine width), records
  `Album.assembled_pdf_path`, and gates submission on a new injectable
  `publish_pdf` hook (local path → public URL) — default `None` keeps the known
  hosting gap, refusing with `UnreachablePdfError` after rendering. Net: the
  default path never invokes WeasyPrint and `assembled_pdf_path` stays NULL there.
  * **Review:** the multi-agent fork panel was unavailable inside the worktree
    harness, so this ran as an adversarial + style **self-review** — no defects;
    verified assemble's page numbering and `LuluPrinter`'s section-page
    reconstruction yield the same page count, and removed a dead `_log`. `poe
    check` green (469/6, incl. Docker pgvector). Live Lulu order still gapped on
    PDF hosting.
* **Caption one-sentence summary + configurable output language (step 42,
  user-requested; commit `504a5e6`).** The opt-in `ClaudeCaptioner` ran too
  verbose, and the user wanted its output language selectable, defaulting to
  Polish. Its system prompt now demands **exactly ONE short summary sentence**
  ("a concise summary, not a description; no lists, no line breaks, no preamble")
  where it used to ask for "a short, natural caption" — the rest of the grounding
  contract (use names, never invent one, never apologise for missing context) is
  unchanged. Language became an argument end to end: `ClaudeCaptioner(language=…)`
  (default `DEFAULT_CAPTION_LANGUAGE = "Polish"`) builds a **per-instance** agent
  from a new `_caption_system_prompt(language)`, so a run captions in one language;
  `Settings.caption_language` (flat, env `CAPTION_LANGUAGE`, mirrors `captioner`);
  `build_captioner(name, *, language=None)` with language-aware registry factories
  (`from_settings(*, language=None)` = flag > env > "Polish"); and a
  `--caption-language LANGUAGE` flag on `build`, threaded through `_resolve_captioner`.
  The offline default `TemplateCaptioner` composes from album metadata (names,
  places, month) and ignores the language, so unset/face-free albums are unchanged.
  * **Review (scaled down per the workflow for a modest, self-contained diff):**
    inline adversarial + style pass, no sub-agent fan-out. No defects. Checked the
    one structural risk — `captioner.py` now importing `album.config.get_settings`
    is one-directional (config deliberately never imports the captioner registry)
    and was already pulled in transitively via `agents/_model`; `from_settings` reads
    only `caption_language`, still no `ANTHROPIC_API_KEY` needed to construct.
  * **Verification:** new `capturing_caption_system_prompt` test double asserts what
    the model actually receives — default-Polish, `language="French"` overrides,
    the one-sentence enforcement is present, and `--caption-language` threads through
    `build_captioner`. `poe check` green (469 passed, 6 skipped, incl. the Docker
    pgvector testcontainers; a stale `testcontainers-ryuk` container-name conflict on
    the first run — env, not code — was cleared and the container suite re-run clean).
    Live-Claude Polish caption spot-check deferred (no key / reachable service) — folds
    into the standing live-integration gap.

## 2026-07-20

* **Total-failure escalation + stage/progress logging (step 41, user-requested;
  commit `f04b4fc`).** The user asked why a stage that hit an error still showed as
  "done". Root cause: the four isolating stages (`quality`/`enhance`/`render`/
  `caption`) deliberately catch every per-photo exception, count it as `failed`, and
  never re-raise (architecture.md's "one bad photo in 3,000 must not sink the run"),
  so `Stage.run()` always reached its unconditional commit — a *systemic* outage
  (Immich down, `darktable-cli` missing, aesthetic model unavailable) that fails every
  attempted photo still reported success, and nothing downstream read the discarded
  `failed` count. Fix: a `Stage._failure_counts(result) -> (failed, attempted) | None`
  hook on the base (default `None`, so the five non-isolating stages are untouched);
  `Stage.run()` raises `StageTotalFailure` (a `RuntimeError` subclass) iff
  `attempted > 0 and failed == attempted`. `attempted` deliberately excludes photos
  short-circuited before any work (`skipped`/`unclustered`/`not_ready`) so an isolated
  bad photo never trips it.
  * **Two defects caught by the review panel, both fixed before merge:**
    (1) *caption false positive* — in the DAG `caption` runs after `enhance`, which has
    already analyzed every clustered photo through the shared `PhotoAnalyzer`, so
    caption's `skipped` means "captioned elsewhere", not a short-circuit. The first
    formula (`captioned + failed`) let a single failing *unclustered* photo escalate a
    99%-captioned album; caption now counts `skipped` toward non-failure
    (`captioned + failed + skipped`), and since `skipped>0` strictly implies ≥1 photo
    already has a caption, it escalates only when nothing anywhere got one.
    (2) *isolation regression* — a bare `RuntimeError` escaped `run_pipelines`'
    per-album isolation and aborted the whole fan-out with a traceback; the dedicated
    `StageTotalFailure` subclass is now caught there, so a systemic per-album failure
    is reported per album (healthy siblings still build) while genuine bugs still
    surface.
  * **Logging:** stage start/done INFO logging hoisted onto `Stage.run()` (logs the
    result `repr`); a time-throttled `ProgressLogger` (stdlib `logging`, injected clock,
    30 s — *not* Prefect's `get_run_logger`, so stages stay runnable outside a Prefect
    context) wired into the four long per-photo loops. The user-added `print()`→logging
    ask: the 4 real `print()`s (all in `cli/runner.py`, the `build` command's output
    contract) route through a dedicated non-propagating `album.cli.output` logger
    (INFO→stdout, WARNING+→stderr, no prefix, streams resolved at emit time so `capsys`
    still captures) — behavior identical, verbosity now controllable; confirmed the
    dedicated-logger approach with the user first. `scripts/build_cat_album.py` (a
    standalone demo whose point is console output) left as `print()`, out of scope.
  * Review: 4 broad-scope agents (2 adversarial correctness, style/reuse, architecture)
    then a focused fix re-review. `poe check` green (465/6, incl. the Docker pgvector
    suite). Live systemic-failure re-verify deferred (no reachable Immich here) — folds
    into the standing live-integration gap.

* **Per-album `select` near-duplicate threshold (step 40, user-requested).** The
  user reported a portrait-session album (`zdjecia-sesja-goszcz`) still landing
  near-identical frames in the book despite dedup. Inspecting the DB row-by-row:
  118 photos collapsed into **one** scene (segment-less, no >90-min gaps), **zero**
  Immich `duplicate_group_id`s, and **64** selected — but every *surviving* pair sat
  at 0.85–0.899 CLIP cosine while every *dropped* photo exceeded 0.9 (avg 0.93). So
  `select`'s near-dup guard was firing correctly; the hard-wired `0.9` cutoff was
  simply too lenient for burst/session content, where consecutive frames differ only
  slightly. The fix makes the cutoff **opt-in per album** rather than lowering the
  global default: `Album.similarity_threshold` (migration `f1a8b6d34c92`, nullable
  `Float`) ← `AlbumYAML.similarity_threshold` (`Field(gt=0, le=1)`); `select` resolves
  it through a new `_active_threshold` property that falls back to the constructor
  default when NULL, so an album that doesn't set it is byte-for-byte unchanged
  (genericity guard). Set `0.82` in the session album's `album.yml`; re-running
  `select` alone against real pgvector cut **64 → 22** picks (max surviving pair
  0.8196, zero over 0.82). Tests use pairs at *exactly* 0.85 cosine so the override
  and the NULL-fallback give opposite outcomes (proving the album value, not the
  default, is honored), plus a **real-pgvector integration test**
  (`test_pgvector_integration.py`) that repeats the override on real 768-d vectors
  through the actual cosine query in a migrated Postgres container — so the new
  path is covered against real embeddings, not just the numpy stub. Caveat carried
  in the tracker: `get_or_create_album` doesn't
  re-import `album.yml` for an already-seen slug, so the existing row was patched
  directly — a changed `album.yml` for an imported album still needs deliberate
  re-import. `poe check` green (449/6).

## 2026-07-19

* **Housekeeping — cat-album demo un-rotted and brought under the gate**
  (`6eb8fdd`, `546d15b`). While auditing `scripts/`, found `build_cat_album.py`
  silently broken: it still called the old `ClusterStage().run(album_id, session)`,
  but stages are now `Stage(album_id, session_factory).run()` (rule #3) — it had
  rotted undetected because `scripts/` sat outside the type/xenon gate (it broke once
  before on the `flow/` move, see below). Repointed to the current API via
  `reuse(session)`, extracted a testable `build_cat_album(output_dir)`, and added
  `tests/album/test_demo_cat_album.py` — a smoke test that drives the demo end to end
  over the committed fixture and asserts a complete book (segment header + all 30
  photos). Then added `scripts` to the [basedpyright](reference/tech-stack.md) include,
  so the demo is now guarded by all three layers (ruff + types + a runtime test) and
  the next break fails `poe check` instead of rotting. Also fixed stale `run`→`build`
  references and a fixture-README path; documented the in-memory `create_all` as an
  intentional rule-#8 exception. `poe check` green (441/6).

* **Docs cleanup — `progress.md` restructured; process/deferral notes moved to their
  canonical homes** (`990d608`). With the build complete, `progress.md` had become a
  frozen audit trail wrapped in live-tracker scaffolding. Collapsed *Outstanding* to a
  one-paragraph summary (the "step 23 subsumed"/completion story had been repeated four
  ways) and trimmed *Current status* to operational facts. Made the **live-integration
  gap the single canonical deferral list** — folded in [open-questions](build/open-questions.md)
  #2/#3 and the LAION within-scene live check, which now point back to it. Relocated
  process conventions *out* of the tracker: the **human-review waiver** is now canonical
  in [implementation-workflow.md](process/implementation-workflow.md) (it had lived only
  in the tracker, which the workflow doc silently contradicted); the ruff-`py314`
  `except` landmine moved to the [working agreement](process/working-agreement.md);
  panel-of-4 / `main`-protected / the parallel-build precedent / resolved landmines were
  dropped as duplicates of homes they already had here or in the workflow doc. Also fixed
  `build-order.md` step 14's dead `run` verb (→ `build`, step 39). Docs-only.

* **Step 23 closed as subsumed + completed-playbook docs archived.** Analyzed whether
  the optional **step 23** (DB-backed flow smoke test) was still worth building: its
  stated goal — the whole Prefect flow over a real Postgres container with
  Immich/Claude/Lulu doubled, asserting cross-stage data flow + real per-stage commits —
  is already met by step 25's `tests/album/flow/test_flow_integration.py` (real
  `pipeline()` end-to-end on the pgvector container, every boundary doubled, all stage
  columns asserted via `expire_all()` read-after-write) plus step 21's
  `test_pgvector_integration.py`. The tracker's "revisit only if a gap remains" condition
  isn't met, so **closed it as subsumed** (no code) rather than write a near-duplicate.
  Moved the three now-complete playbook docs into `build/archive/`
  (`testcontainers.md`, `external-library-ingestion.md`, `step-39-cli-package-split.md`),
  rewriting every cross-bundle link to `/build/archive/…` and reshuffling the `index.md`
  entries into the Archive section. Encoded the archiving mechanics as a standing
  convention in [implementation-workflow.md](process/implementation-workflow.md) so it
  isn't re-derived next time. Docs-only; no `src/`/test changes.

* **Step 39 (follow-up) — split `cli.py` into an `album/cli/` package** (`3c1177f`;
  [plan](build/archive/step-39-cli-package-split.md)). The step-39 `build` command worked and
  was `poe check`-green, but lived in a single ~370-line `cli.py` mixing four
  responsibilities. Split into a package with one job per module: `parser.py` (argparse
  only), `sources.py` (the `AlbumSource` strategies + `select_source`), `runner.py`
  (`BuildRunner` + the pure `run_pipelines` transform), and `__init__.py` (the public
  `main` entrypoint). A **pure structural refactor — no CLI behaviour change** (same
  `build` surface, flags, exit codes, output): function bodies moved verbatim, three
  test-driven names went public (`select_source`/`prepared_from_outcome`/`run_pipelines`),
  and `_build`/`_report` became `BuildRunner`. The parser drops the now-single-command
  `func` dispatch default but keeps `set_defaults(parser=…)`, so usage errors still exit
  2 against the build subparser; entry point `album.cli:main` resolves unchanged. Tests
  retargeted patch-at-use-site (`pipeline`/`get_settings` → `album.cli.runner`, the
  `ingest_*`/`load_manifest`/`patch_lead_faces` → `album.cli.sources`). Two adversarial
  sub-agent reviews (correctness; style/reuse) found no defects — bodies verified
  byte-for-byte identical. Living docs (folder-structure tree, architecture note) point
  at the `cli/` package. `poe check` green (440 passed, 6 skipped). Logged as a step-39
  follow-up (not a new step number), per the user's framing.

* **Step 39 — unify the CLI into one `build` command** (`6f3c5c1`). User-requested cleanup of a
  CLI that had grown verbose and scattered across `generate`/`run` subcommands with parallel
  `_generate_single`/`_generate_manifest`/`_run` handlers. Now a single `photobook-generator build`
  with two phases — **ingest** (default on) and **pipeline** — that become an `AlbumSource` strategy
  (an ABC) chosen once from the parsed args: `build <dir>` → `DirectoryIngestSource`; `--manifest` →
  `ManifestIngestSource` (fan-out); `--no-ingest --album <slug>` → `NoIngestSource` (skip ingest, run
  an existing `album.yml`, the old `run`). `_select_source` decides the strategy and validates the
  argument combination; each strategy returns `PreparedAlbum`(s), and one shared `_run_pipelines` +
  `_report` driver runs the pipeline over them and prints a single combined summary (uniform for one
  album or N). Surface decisions confirmed with the user up front (via a quick question set): the new
  verb is **`build`**, skip-ingest is the explicit **`--no-ingest`** flag, and it's a **clean break —
  no `generate`/`run` aliases**. Two intended behavior changes fell out: argument *misuse* is now a
  uniform argparse usage error (exit 2) rather than the old ad-hoc exit 1, and the single/no-ingest
  paths print the same book-count summary the fan-out already did. `test_cli.py` was rewritten around
  the three strategies; `test_genericity.py` retargeted from the removed `_run_album_pipelines` to
  `_run_pipelines` + `_prepared_from_outcome`; the poe `run`/`generate` tasks collapsed to `build`;
  and every living doc (`README.md`/`README.html`, `AGENTS.md`, `working-agreement.md`,
  `folder-structure.md`, the ingest stage doc, the external-library playbook) was updated to the new
  command (historical log/archive/build-order entries left as-is). Inline review across the
  correctness/reuse/simplification/altitude angles: no defects; the complexity gate split
  `_select_source` (rank B → A). `poe check` green (440 passed, 6 skipped). Logged as build step 39
  at the user's request.

* **Step 38 — external-library ingest: the `--ingest-mode` flag (closes the playbook)** (`55eb1ad`;
  [external-library-ingestion.md](build/archive/external-library-ingestion.md)). Exposes the step-37
  `AssetRegistrar` seam on the CLI: `generate --ingest-mode {upload,external}`, with precedence
  flag > `IMMICH__INGEST_MODE` env > `upload` default (the `--captioner` pattern). It is a
  **run-level** mode — how bytes reach Immich — so it applies to every album including the
  `--manifest` fan-out, and is deliberately *not* in the per-album-flag rejection set. `INGEST_MODES`
  centralizes the valid set that the CLI `choices` and `build_registrar`'s dispatch both read;
  `build_registrar` gained an `ingest_mode` override threaded from `ingest_directory`/
  `ingest_manifest`. User docs landed: a README "Scan photos in place (no second copy)" section and
  an ingest stage-doc note, both covering setup (uncomment the `${PHOTOS_LOCATION}:/external:ro`
  mount, `IMMICH__EXTERNAL_HOST_ROOT`, the admin-key requirement) and the core trade-off — the
  originals must stay mounted for the album's life or Immich marks the assets offline. Default
  upload path unchanged (byte-for-byte); new CLI flag-threading + flag-over-env precedence tests,
  plus a `--help` smoke confirming the flag is registered. Inline review across the
  correctness/reuse/simplification/altitude angles: no defects. `poe check` green (438 passed, 6
  skipped). This **completes the external-library / scan-in-place ingestion playbook** (steps
  36–38); a live external run against a real Immich + bind mount stays folded into the standing
  live-integration gap (no reachable Immich in this sandbox). Only the optional step 23 remains on
  the build order.

* **Step 37 — external-library ingest: the `AssetRegistrar` seam** (`b09f994`;
  [external-library-ingestion.md](build/archive/external-library-ingestion.md)).
  Wires the mode swap the step-36 plumbing set up, still **opt-in** (default unchanged). The one
  line `_ingest` used to run inline — `{path: upload_asset(path)}` — is now an injected
  `AssetRegistrar.register(directory, image_paths) -> {host path → asset id}`; everything downstream
  (`create_album`, segments, materialization, the pipeline) reads only that map, so the whole
  feature lives behind the seam. Two implementations: `UploadRegistrar` (today's per-file upload,
  extracted verbatim, the default) and `ExternalLibraryRegistrar` (ensure one shared library reused
  by import path → scan → wait the `library` queue out → map each host photo to its scanned asset id
  by matching Immich's container-side `originalPath` back through `PathMapping`, filtering the
  tree-wide library down to this album's photos, failing fast and naming any photo the scan didn't
  return). The readiness poll was generalized to a shared `_wait_for_queues(get_jobs, queues, …)` so
  each strategy declares the queues it waits on — the ingestor's EXIF wait and the registrar's scan
  wait now share one poll. `build_registrar` maps the `ingest_mode` string → registrar (validation
  kept out of `config.py`, mirroring `build_captioner`); `ingest_directory`/`ingest_manifest` build
  it from settings. **Live-verify caveat:** the scan is async, so the `library`-queue poll's initial
  idle check could race a not-yet-enqueued scan job on a real Immich — folded into the standing
  live-integration gap alongside the step-36 `/libraries` shape check. `poe check` green (434 passed,
  6 skipped, incl. the pgvector testcontainer now that Docker is up). Inline review across the
  correctness/reuse/simplification/altitude angles: no defects; only stale "upload" doc comments
  refreshed to "register". Complexity gate caught `_map_assets` at rank B (three comprehensions) →
  split the `originalPath` index + the missing-photos error into module helpers.

* **Docker re-verify (step 36 close-out).** With the Docker daemon back up, the container-backed
  suite that step 36 had to merge without (`test_pgvector_integration.py`/`test_similarity.py`,
  plus the flow integration test) now passes — 44 in the targeted run; tracker note updated. The
  earlier `poe check` failure was a stale testcontainers-ryuk container name conflict (a leftover
  from a manual run), cleared with `docker rm -f`, not a code issue.

* **Step 36 — external-library (scan-in-place) ingest: plumbing** (`abf4779`;
  [external-library-ingestion.md](build/archive/external-library-ingestion.md)). Foundation for a
  new **opt-in** ingest mode that references photos on disk instead of copying them into
  Immich — no behavior wired yet. Adds: `ImmichSettings.ingest_mode`/`external_host_root`/
  `external_container_root`; a pure `PathMapping` (`immich/paths.py`) translating a host
  path ↔ the container `originalPath` (fails fast outside the mount root); the
  `ImmichClient` External-Library surface (`get_current_user_id`/`get_libraries`/
  `create_library`/`scan_library`/`get_library_assets`) + `originalPath` on `ImmichAsset`,
  with `get_album_assets`/`get_library_assets` sharing a new `_search_metadata` helper; and
  an **opt-in, commented** `${PHOTOS_LOCATION}:/external:ro` mount + `.env.example` block so
  default `poe up` is unchanged. Decisions confirmed with the user (upload stays default;
  one shared reused library; originals must stay mounted — the accepted cost of not
  copying; no content-dedupe). `/libraries` methods are modelled on the documented API and
  respx-tested only — exact shape flagged for live verification (standing live-integration
  gap). `_wait_for_readiness` scan-queue extension deferred to step 37. Static gate + all
  non-container tests green; container tests blocked by an unresponsive Docker daemon
  (env, not code). Two review sub-agents (adversarial correctness + style/reuse): no
  blockers/majors; nits applied (empty-scan-body assertion, machine-independent
  `from_settings` test, docstring closings, doc/anchor fixes).

* **Step 35 — folder-structure-driven ingestion docs + genericity gate** (`63095ea`;
  [step-35-docs-verify.md](build/archive/step-35-docs-verify.md)). Closes the
  [folder-structure-driven ingestion playbook](build/archive/folder-structure-ingestion.md)
  (steps 32–35 all merged). User-facing docs landed (README "Multiple albums from one
  folder tree", `album-definition.md`'s "three sources, one shape" note,
  `folder-structure.md`'s `ingest.py` fan-out line, and a consolidated **As-built**
  preamble on the umbrella reconciling the 32–35 deviations). The verification gate is
  two-part: (1) a **scoped token guard** asserting `manifest.py`/`text.py`/`ingest.py`
  carry no trip/day/itinerary/occasion vocabulary — deliberately *not* a whole-`src/`
  grep, because the rest of `src/` legitimately *discusses* genericity ("nothing here
  knows the occasion") and uses "round-trip", so a blanket grep would false-positive; and
  (2) an **end-to-end test** over a committed two-album fixture tree
  (`tests/fixtures/manifest_tree`) that drives the real `load_manifest` + `_ingest_all` +
  materialization + a patched pipeline, proving N albums → N `album.yml` (one
  folder-windowed, one zero-segment) → N pipeline runs — the `smith-wedding` genericity
  precedent applied to fan-out. `DatingIngestClient` was hoisted to `tests/doubles.py`
  (now shared) and `ingest.py`'s module docstring dropped its lone "occasion" mention so
  the guarded modules stay token-clean. 2-agent review (adversarial + style/docs): the
  adversarial pass caught a stray `open-questions.md` frontmatter corruption (`1---`) —
  an accidental edit unrelated to the step — which was reverted; otherwise the gate and
  docs were found sound. `poe check` green (408 passed, 6 skipped). Live multi-album
  fan-out against a real Immich remains folded into the standing first-live-run gap; the
  fixture-tree e2e is the offline stand-in.

* **Step 34 — manifest fan-out + `generate --manifest`** (`a2b5766`;
  [step-34-fanout-cli.md](build/archive/step-34-fanout-cli.md)). `ingest_manifest` ingests
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

* **Step 33 — folder-derived segments in `ingest.py`** (`28aa809`;
  [step-33-folder-segments.md](build/archive/step-33-folder-segments.md)). New
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

* **Step 32 — ingest manifest schema + loader** (`50c83ab`;
  [step-32-manifest-schema.md](build/archive/step-32-manifest-schema.md)). New pure
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
  New [folder-structure-ingestion.md](build/archive/folder-structure-ingestion.md) umbrella
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
  [progress.md](build/progress.md) Outstanding.

* **Step 30 — repeatable `--lead-face` CLI flag + multi-photo lead resolution**
  (`c695e09`; [step-30-cli-lead-faces.md](build/archive/step-30-cli-lead-faces.md)). `generate`/`run`
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

* **Step 31 — real LAION/open_clip aesthetic scorer wired** (`0cafbdb`;
  [step-31-laion-aesthetic-scorer.md](build/archive/step-31-laion-aesthetic-scorer.md)). New
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
  [working-agreement.md](process/working-agreement.md): a "Be concise" principle
  over all output, and a Git-workflow subsection dividing labour — commit bodies own
  the per-diff mechanical record, `log.md` owns the cross-commit narrative/decisions
  and points into git via SHAs rather than restating it. Decided **against** folding
  the log into commit history via a rewrite (entries don't map 1:1 to commits, bundle
  links wouldn't resolve, and a rewrite invalidates the SHAs the log cites).

* **README rewritten as a one-page brief + pipeline-execution destale (user-directed).**
  `README.md` → a brief (quick start + links into `knowledge/`). Fixed
  `pipeline-execution.md`/`.html`, which still framed the DAG fan-out as *proposed*
  (it shipped in step 25); corrected stage count (11 → 10).

* **Docs refactor + trim (user-directed; `ed8c7cb`).** Made top-level files thin
  pointers to the `knowledge/` bundle (source of truth); de-staled and de-duplicated.
  Deleted `HANDOVER.md` + `PROJECT_BRIEF.md`; added a 3-line `CLAUDE.md`. Assigned a
  canonical owner per repeated fact (TaskUnit → quality-practices; commit/fanout
  invariants → pipeline-execution; poe table → working-agreement), others cross-link.
  Net −1419 lines, markdown only.

## 2026-07-17

* **Step 29 — offline classical replacement for the Claude vision call** (`92b020e`;
  [local-analysis.md](build/archive/local-analysis.md)). Split the fused per-photo vision
  call into two injectable collaborators in a new `album.analysis` package — a
  `ClassicalToneEstimator` (tone now always classical) and a `Captioner` seam
  (`TemplateCaptioner` offline default + opt-in `ClaudeCaptioner`), composed by
  `PhotoAnalyzer`. Selectable per run (`--captioner` / `CAPTIONER`). This removes the
  pipeline's **last external dependency** — a default run needs no API key. Accepted
  the `--force --captioner claude` ~2× cost on clustered photos. Live-Claude
  spot-check deferred.

* **Review panel widened to 4 (user-directed).** [implementation-workflow.md](process/implementation-workflow.md)
  §6: up to 4 broad-scope review sub-agents — 2 adversarial correctness, 1
  style/reuse, 1 architecture — with "scale down for small diffs" (was a cap of 3).

* **Ruff trailing commas + `SimilaritySearch` → `Sequence` (user-directed).**
  Widened `db/similarity.py` read-only inputs to `Sequence` (`f946a3f`). Enabled
  `COM812` + a repo-wide trailing-comma reformat (`1457ffb`), then moved `COM812` to
  `lint.ignore` (`21bbab4`) after ruff warned it may conflict with the formatter —
  the reformat stays and the formatter's magic trailing comma keeps collections
  vertical, but trailing commas are no longer linter-enforced.

* **Open question 7 resolved — shared `selected_photos` query hoisted** (new
  `db/queries.py`; [open-questions.md](build/open-questions.md) Q7). The
  "selected photos, ordered by id" read, copied across caption/enhance/render, is now
  one `selected_photos(album_id, session)` free function — chosen over a Repository
  (SQLAlchemy's Session already gives Unit of Work / Identity Map). Takes the caller's
  session, so it composes with fan-out session ownership. Fixed the latent drift Q7
  warned of — `caption` was missing `.order_by(Photo.id)` (determinism only).

* **Proposal recorded — offline analysis** (new [local-analysis.md](build/archive/local-analysis.md)).
  Design proposal to drop the last external-API dependency by splitting the Claude
  call into classical tone (CV) + metadata-template caption, keeping a VLM injectable
  as an optional "rich caption" mode. Not cost-driven (~$1–$4/album) — offline /
  no-key / deterministic. Implemented as step 29 above.

* **Open question 5 resolved — AI-pass cost estimate** (new
  [ai-cost-estimate.md](build/archive/ai-cost-estimate.md); Q5). The full Claude vision pass
  ≈ **$1–$4 per 300–500-photo album** (one idempotent call per selected photo).
  Verdict: acceptable, no cost control needed; caching not worth it (system prompt
  below Sonnet's cache floor). Metered re-check deferred to the first live run.

* **TestContainers step 24 — offline Immich ML integration** (`f990248`;
  [testcontainers.md](build/archive/testcontainers.md)). Verifies Immich's ML paths (faces,
  smart search) against a real 4-service stack with **no model fetched from the
  internet at test time**: a build-time `snapshot_download` bakes CPU `.onnx` models
  into an image (`poe bake-immich-ml`, the one internet-touching step), driven via
  testcontainers `DockerCompose`. Uses `skimage.data.astronaut()` (public-domain, no
  personal photo). Closes step 17's live face-detection deferral. Opt-in
  (`IMMICH_ML_INTEGRATION=1`), skipped in `poe check`. Follow-up: baked image ~3.1 GB
  (dominated by the base image; little to reclaim in our layer).

* **Enrichment step 20 — rating + geocoded place; OCR deferred** (`698757f`;
  [enrichment.md](build/archive/enrichment.md)). Two optional Immich signals read from data
  `sync` already fetches (no extra call): manual star `rating` and reverse-geocoded
  place. New nullable `Photo` columns + one migration. `select` gains a soft per-star
  `rating_boost` (reorders comparable photos only; never drops a lead; inert for an
  unrated album). `caption` grounds on the photo's place. `cluster` deliberately
  untouched. **OCR deferred** — a 2nd per-asset N+1 for a "minor" signal, absent from
  the pinned v3.0.2 API. **Completes the Immich-enrichment playbook.**

* **Build step 28 — document-driven segments (`--source-document`)** (`e398662`;
  [single-command-ingestion.md](build/archive/single-command-ingestion.md)). New
  `agents/segments.py`: a PydanticAI agent that turns a schedule PDF into the album's
  `segments` (text via `pypdf`, falling back to page images for a scanned PDF). Output
  reuses `SegmentYAML`; the `album.yml` loader stays the correctness authority (UTC
  normalization, ordering/non-overlap). Wired into `generate` after the readiness
  poll, grounded on the photos' capture-date range. A loose extraction **fails fast**
  with a clear `IngestError` (caught in review) rather than crashing deep in the
  pipeline. **Genericity held** — only the system-prompt *string* names example
  document kinds. Live-Claude accuracy spot-check deferred. **Completes the
  single-command-ingestion playbook (26–28).**

* **Build step 27 — single-command `photobook-generator generate <dir>`** (`2b0bd56`).
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

* **Build step 26 — Immich album creation in the client** (`1e69505`). Adds
  `create_album` (`POST /albums`) and `get_jobs` (`GET /jobs`, the readiness signal
  step 27 polls) to `ImmichClient`. Boundary models stay lean (per the
  `ImmichAsset`/`ImmichFace` pattern). Modelled against the OpenAPI contract, not a
  live capture — re-verify on the first live ingestion.

* **Plan added — [single-command ingestion](build/archive/single-command-ingestion.md)**
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
  [`pipeline-execution.md`](foundations/pipeline-execution.md) DAG: the four
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
  [`immich-dependency-decision.md`](build/immich-dependency-decision.md). Maps what
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

* **Doc — [Pipeline execution shape](foundations/pipeline-execution.md).** Documents
  the stage DAG (two parallelisable fan-out groups), the critical path, and a sketch
  of the Prefect own-session-per-task change to parallelise it (with pool /
  read-after-write / test caveats). Ships a self-contained `.html` diagram.
  (Reference only then; implemented as step 25.)

* **TestContainers step 21 — pgvector integration suite** (`ac2cd14`). First step of
  the [playbook](build/archive/testcontainers.md): promotes the DB boundary from in-memory
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
  First step of the [enrichment playbook](build/archive/enrichment.md); adopts Immich's
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
  (user-directed; resolves [open question #6](build/open-questions.md)). Web research
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
      value — **not taken**. [Open question #1](build/open-questions.md) (no
      embeddings on the API) re-confirmed.

* **Review process: cap sub-agent review at ≤3 broad-scope agents** (user-directed,
  token-saving). `implementation-workflow.md` §6 + `AGENTS.md`: a **maximum of 3**
  essential broad-scope agents (correctness/cleanup/tests), fewer for a small diff.
  (Later widened to 4 — see 2026-07-17.)

* **Printer abstraction + HTML default output** (post-step-16, user-directed).
  Generalized `fulfill` from a Lulu-only submitter to a pluggable
  [`Printer`](stages/fulfill.md); a browsable self-contained HTML book is now the
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
      [immich-api.md](reference/immich-api.md).
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
  (`Album`/`Segment`/`Photo`, mirroring [data-model.md](reference/data-model.md)); the
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
* **Process:** added the per-step [implementation workflow](process/implementation-workflow.md)
  and a living [progress tracker](build/progress.md); never commit to `main`.
* **Decision:** local `pre-commit` hooks at scaffold (`no-commit-to-branch`, ruff,
  basedpyright), full pytest left to `poe check` + CI.
* **Build step 0 — scaffold** (`11d290f`). `uv` project (Python 3.14), ruff/basedpyright/
  pytest + poe tasks, the package skeleton (`config.py`/`definition.py`/`flow.py`
  deferred to steps 3/14), `.pre-commit-config.yaml`. Verified a direct commit to
  `main` is rejected by the hook.

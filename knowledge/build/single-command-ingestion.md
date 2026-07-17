---
type: Playbook
title: Single-command album generation (photos dir → book) + document-driven segments
description: Plan for a `photobook-generator <dir>` one-shot that uploads a local photo directory into Immich, materializes an album.yml, and runs the pipeline — plus an optional `--source-document=doc.pdf` that extracts segments from a schedule/itinerary document via a PydanticAI agent.
tags: [playbook, build-order, cli, ingest, immich, segments, agent, pdf, ux]
timestamp: 2026-07-17
---

# Single-command album generation + document-driven segments

A follow-on to the [build order](/build/build-order.md) and
[enrichment playbook](/build/enrichment.md), capturing two user-requested UX
features and how they fit the current architecture without breaking its
invariants. **This is a plan/handover, not yet-built code.**

## The two asks

1. **One command, point at a folder.** Today a run needs a hand-written
   `data/albums/<slug>/album.yml` carrying a real `immich_album_id`, which means
   the user must first upload photos into Immich and create an album by hand (the
   manual [`ingest`](/stages/ingest.md) step). The ask is to collapse all of that
   into a single command:

   ```sh
   photobook-generator .
   photobook-generator ../some/dir/with/photos
   ```

   → point at a directory of photos on disk, get an HTML book out, no manual
   Immich upload and no hand-authored `album.yml`.

2. **Document-driven segments.** Extract an album's `segments:` (the time/place
   chapters) from a source document — a trip itinerary, a wedding run-sheet, a
   festival lineup — instead of typing them by hand:

   ```sh
   photobook-generator . --source-document=./some/doc.pdf
   ```

## Feasibility verdict

**Both are feasible and additive** — no stage logic changes, no data-model
change, and the pipeline DAG is untouched. Together they are roughly three small
build steps (see [Build steps](#proposed-build-steps)). The design keeps
`album.yml` as the single source of truth: the new command *materializes* an
`album.yml` and then calls the **existing** `pipeline(album_slug=...)`, rather
than adding a second way to define an album.

**What "single command" does and does not remove.** It removes the manual Immich
upload + album creation + `album.yml` authoring. It does **not** remove the
local infrastructure: Immich, both Postgres DBs, and Prefect still have to be up
(`uv run poe up`; `uv run poe migrate`), and `IMMICH__API_KEY` +
`ANTHROPIC_API_KEY` still have to be set in `.env`. This is a UX wrapper over the
existing boundary, not a new zero-dependency runtime. State that plainly in
`--help` and the README so the "one command" promise isn't over-sold.

## Current gap — what stops `photobook-generator .` today

| Need | Status today | Work |
|---|---|---|
| Upload each file in a directory to Immich | `ImmichClient.upload_asset(path)` exists (built for `lead_faces`, content-deduped, safe to retry) — but only one file | Walk the directory, call it per image, collect asset ids |
| Create an Immich album from those assets | **Missing** — client has no album-creation method | Add `create_album(name, asset_ids)` (`POST /albums`) |
| Wait for Immich's async EXIF/face/dedupe jobs before `sync` | Partially solved — `LeadFaceResolver` already polls async face detection | Reuse that polling shape; poll job readiness before running the pipeline |
| Produce an `album.yml` without the user writing one | `AlbumDefinition`/`AlbumYAML` load it; nothing *writes* one | Emit a minimal `album.yml` (slug/title/immich_album_id[/timezone][/segments]) |
| A `photobook-generator <dir>` entry point | Only `album run --album <slug>` exists (`[project.scripts] album`) | Add a console script + a positional-directory command |

Everything downstream of `sync` is already directory-agnostic — it reads our DB,
scoped by `album_id`, exactly as it does for a hand-authored album. So the whole
feature lives **in front of** `sync`.

## Feature A — single-command ingestion

### Flow

`photobook-generator <dir>`:

1. Resolve `<dir>` and pick a **slug** + **title** (see
   [Title resolution](#title-resolution)). `.` resolves to the current
   directory's name.
2. Walk `<dir>` for image files (jpg/jpeg/png/heic/…; decide the set and whether
   to recurse). Upload each via the existing `upload_asset` (content-dedupe makes
   re-runs cheap and idempotent). Collect the returned asset ids.
3. `create_album(title, asset_ids)` → the new `immich_album_id`.
4. **Wait for Immich to finish processing** (EXIF at minimum — `sync`/`cluster`
   need capture timestamps; faces/dedupe are best-effort). Poll the jobs/asset
   state, mirroring `LeadFaceResolver`'s async-detection poll with a tunable
   timeout; on timeout, warn and continue rather than hang (undated photos are
   already handled downstream — `sync` flags them, `cluster` has a NULL-timestamp
   path).
5. Write `data/albums/<slug>/album.yml` with `slug`, `title`, `immich_album_id`,
   and (if resolvable) a `timezone`. If `--source-document` was given, fill
   `segments:` from Feature B.
6. Call `pipeline(album_slug=slug)` — the existing flow, unchanged.
7. Print the path to `data/albums/<slug>/book/index.html`.

### New/changed code

- **`src/album/immich/client.py`** — add `create_album(name, asset_ids) -> str`
  (`POST /albums`, returns the album id) and, if a two-step create is cleaner,
  `add_assets_to_album(album_id, asset_ids)` (`PUT /albums/{id}/assets`).
  Optionally `get_jobs()` (`GET /jobs`) for the readiness poll. Add the request/
  response Pydantic models next to `ImmichAsset`/`ImmichDuplicate`, and confirm
  the exact endpoints/shapes against [Immich API notes](/reference/immich-api.md)
  (the spec has moved since it was pinned at v3.0.2 — the same caveat the
  enrichment spike flagged). Cover with `respx` fixtures like the rest of the
  client.
- **`src/album/ingest.py`** (new top-level module — *not* a pipeline stage; it
  runs before `sync` and is the automated form of the manual
  [`ingest`](/stages/ingest.md) step). Owns: directory walk → per-file upload →
  `create_album` → readiness poll → `album.yml` materialization. Talks to
  `ImmichClient` + the filesystem; contains no DAG/Prefect logic. Returns the
  slug it wrote, which the CLI hands to `pipeline`.
- **`src/album/cli.py` + `[project.scripts]`** — add a `photobook-generator`
  console script with a **positional directory** and `--source-document`,
  `--slug`, `--title`, `--timezone` options. Keep `album run --album <slug>` as
  the album.yml-first path (unchanged). Reasonable shape: `photobook-generator`
  = "dir → book"; `album run` = "existing album.yml → book". Keep `cli.py` thin
  (arg-parse + one call into `ingest` then `pipeline`), per its current contract.

### Title resolution

The user never *has* to pass a title, but can. Resolve it by a precedence chain —
explicit wins, then progressively cheaper inference:

1. **`--title`** if given — explicit always wins. (`--slug` overrides the slug
   likewise; default slug is the directory name slugified.)
2. **From `--source-document`** if given — a run-sheet/itinerary almost always
   names the event. The [segments agent](#the-extraction-agent) is already
   reading that document, so it should return a **suggested title alongside the
   segments** in the same call (add a `title: str | None` to its output model) —
   no extra round-trip. The preferred autogeneration path when a doc is present.
3. **From photo metadata** — deterministic, no LLM: the EXIF capture **date
   range** (always available from Immich) plus the **reverse-geocoded place**
   (`city`/`state`/`country` on `exifInfo`) → e.g. "Lisbon · May 2026".
4. **Directory name** — `../trip-2026` → "Trip 2026".
5. **Slug** — last resort.

Caveats: the *place* half of #3 depends on Immich's geocoding surface, which the
pipeline does not consume yet ([enrichment](/build/enrichment.md) step 20,
`todo`); until then the metadata title is **date-only** ("May 2026"), which works
today. A pure **vision-LLM title** (sample photos → ask Claude to name the album)
is possible but deliberately **not** the default — non-deterministic and it adds
a call before `select` runs; keep it as an optional later add-on. Genericity
holds: a place+date title names no occasion concept.

### Design notes / guards

- **`album.yml` stays the source of truth.** The command *generates* it and then
  uses the normal load path (`get_or_create_album` → `AlbumDefinition`), so the
  materialized file is inspectable and editable, and there is still exactly one
  way the pipeline defines an album. Note `get_or_create_album` is
  *load-once-per-slug*: re-running `photobook-generator` on an existing slug
  reuses the existing `Album` row and will **not** re-read a regenerated
  `album.yml` (documented caveat in `_flow.py`). Decide the re-run story — new
  slug, explicit `--force`, or a deliberate re-import path — and document it.
- **Genericity holds trivially.** `ingest.py` deals in "a directory of images"
  and "an Immich album"; it introduces no occasion-specific concept, so the
  [folder-structure rule](/reference/folder-structure.md) ("nothing in `src/`
  references trips/itineraries/days") is unaffected.
- **Idempotency.** `upload_asset` already returns the existing id for duplicate
  bytes; make `create_album` tolerant of (or skip when) the album already exists,
  so a re-run after a mid-way failure self-heals like the `lead_faces` path.

## Feature B — source document → segments

### Skill vs. app feature — recommendation

Two ways to read "a Claude skill that turns a PDF into a segments file":

- **A Claude Code skill** (`.claude/skills/…`) — an instruction bundle *this
  agent* follows at dev time. Zero app code, no runtime dependency, good for a
  one-off where a developer is already in a Claude Code session. But it is **not**
  available to an end user running `photobook-generator`, isn't reproducible, and
  isn't testable in the suite.
- **An app feature** (`--source-document`) — a PydanticAI agent inside the app
  extracts segments during ingestion. Reproducible, unit-testable via
  `TestModel`/`FunctionModel` (the pattern `agents/analysis.py` already uses),
  available to every user, and integrated into the one command.

**Recommendation: build the app feature.** It is the durable, user-facing,
testable path, and the pieces are already in place — the app is a PydanticAI
app, and **`pypdf` is already a dependency** (`pyproject.toml`), so PDF text
extraction needs no new package. A `.claude/skills/` entry, if wanted, should be
a *thin convenience wrapper that invokes the same command* (`photobook-generator
… --source-document=…`) — not a parallel re-implementation of the extraction
logic. Build the app feature first; add the skill only as optional dev-time
sugar.

### The extraction agent

- **`src/album/agents/segments.py`** (new) — a second PydanticAI agent, sibling
  to `analysis.py`. Output model is the album's own segment shape: a wrapper over
  `list[SegmentYAML]` (reuse `definition.SegmentYAML`, or a dedicated output
  model mapped onto it). Input: the document's extracted text (via `pypdf`; for
  a scanned/image PDF, fall back to sending page images as `BinaryContent`, the
  way `analysis.py` sends photos). Pure and DB-free, like `analyze_photo` — the
  caller (`ingest.py`) owns writing the result into `album.yml`. The output model
  should also carry an optional **`title`** (the event name, if the document
  states one) — it feeds precedence #2 of [Title resolution](#title-resolution)
  at no extra cost, since the document is already being read.
- **Grounding to resolve relative dates.** Itineraries often say "Day 1 —
  Dubrovnik" with no year, or "May 2" with no zone. Run extraction **after**
  upload + EXIF readiness so the agent can be handed the **photos' capture date
  range** (from Immich) and the album `timezone` as anchors, turning "Day 1" into
  a real UTC-normalizable window. Emit ISO timestamps; `AlbumYAML`'s existing
  validator then normalizes them to UTC and enforces non-overlap / ordering — so
  the agent can be a touch loose and the loader stays the source of truth for
  correctness (same posture as `analysis.py`'s bounded fields).
- **Genericity constraint — load-bearing.** The document may *be* a trip
  itinerary, but nothing in `src/` may name that concept. The module, model,
  fields, and prompt talk about **"a document describing time/place windows" →
  segments** — never "itinerary", "day", "trip". The prompt can list mixed
  examples (trip, wedding, festival) precisely to keep the extraction generic.
  This is the same guard `select`/`caption` already respect; enforce it in review
  and tests.

### Wiring

`--source-document` is read by the CLI, passed to `ingest.py`, which calls the
segments agent and drops the result into the `segments:` block of the
`album.yml` it materializes. Without the flag, `segments:` is simply omitted —
an unstructured album, already fully supported. Independently useful for an
existing album too (regenerate/fill its segments), though mind the load-once
re-run caveat above.

## Proposed build steps

Delivered per the [implementation workflow](/process/implementation-workflow.md)
(branch, TDD, sub-agent review, `poe check` gate, squash-merge). Suggested split:

1. **Immich album creation in the client** — `create_album` (+ maybe
   `add_assets_to_album`, `get_jobs`) with respx fixtures. Small, self-contained,
   verifiable against the live spec.
2. **`ingest.py` + `photobook-generator` entry point** — directory walk → upload
   → create album → readiness poll → materialize `album.yml` → call `pipeline`.
   Delivers Feature A end-to-end (unstructured album). Tests: fake `ImmichClient`,
   a temp dir of sample images, assert the written `album.yml` and that
   `pipeline` is invoked with the right slug.
3. **`agents/segments.py` + `--source-document`** — the extraction agent
   (`TestModel`/`FunctionModel` tests, a sample PDF fixture) and its wiring into
   `ingest.py`. Delivers Feature B. Optional 3b: a thin `.claude/skills/` wrapper.

## Risks / open questions

- **Immich readiness timing.** How long to poll for EXIF/faces before running the
  pipeline, and what the jobs/asset-status endpoint actually reports — verify on
  a live Immich (the same live-Immich gap open since steps 4–5; see
  [progress](/build/progress.md)). Degrade gracefully on timeout.
- **Re-run semantics** for an already-imported slug (load-once caveat above) —
  pick and document the story before shipping.
- **Which image formats** to ingest (HEIC/RAW?) and whether to recurse into
  subdirectories.
- **Scanned vs. text PDFs** — `pypdf` yields no text for image-only PDFs; the
  page-image fallback path needs its own test.
- **Segment extraction accuracy** needs a live-Claude spot-check — the same
  deferred manual-verification pattern as `caption`/`enhance` (no
  `ANTHROPIC_API_KEY` in the current sandbox).

## Out of scope

Physical printing (`LuluPrinter` and the open PDF-hosting gap — see
[progress](/build/progress.md)); the deferred LAION/open_clip quality scorer;
any change to the pipeline stages or data model.

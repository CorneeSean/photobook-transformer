# Photo Album Pipeline

A batch pipeline that turns a raw dump of photos into a curated, enhanced,
captioned, print-ready photo book — with minimal manual work. It's
**event-type agnostic**: a trip, a wedding, a baby's first year, a festival are
all just *albums* (an `Album` with optional, named `Segment`s).

The [`knowledge/`](./knowledge/index.md) bundle is this project's source of
truth — domain model, architecture, data model, and the step-by-step build
order. [`AGENTS.md`](./AGENTS.md) orients coding agents working in this repo.

## Quick start path

Photos don't go into the pipeline directly — they first live in **Immich** (a
self-hosted photo library that runs locally in Docker). The flow is: **your
folder → Immich album → pipeline → `index.html`.**

```sh
# 0. Install — once
uv sync && uv run pre-commit install
cp .env.example .env

# 1. Start the local stack (Immich + Prefect + Postgres)
uv run poe up
uv run poe migrate
```

Open `http://localhost:2283`, create the admin account (first visit only), then
grab an API key (*Account Settings → API Keys → New*) and put it in `.env` as
`IMMICH__API_KEY`.

```sh
# 2. Build the book — point it at a directory of photos; one command does
#    upload, Immich album creation, album.yml, and the full pipeline run
uv run photobook-generator build ./path/to/photos
```

Your book lands at `data/albums/<slug>/book/index.html` — open it in any
browser. `ANTHROPIC_API_KEY` is optional (only for the opt-in `--captioner
claude` upgrade); the default run is fully offline. `build` also takes
`--slug`, `--title`, `--timezone`, `--force`, `--source-document schedule.pdf`
(extracts `segments` from a trip itinerary / run-sheet / line-up), and
`--ingest-mode` (see [Scan photos in place](#scan-photos-in-place-no-second-copy)).
To hand-author `segments`/`lead_faces` instead, write `data/albums/<slug>/album.yml`
yourself and run `uv run photobook-generator build --no-ingest --album <slug>`
(skips the ingest phase and just runs the pipeline) — see
[Album definition](./knowledge/reference/album-definition.md) and
[Single-command ingestion](./knowledge/build/single-command-ingestion.md).

### Multiple albums from one folder tree

Point `build` at a directory that holds several albums and add `--manifest` to
fan it into **N independent books** in one run — one Immich album, one `album.yml`,
one pipeline run, one book per declared album:

```sh
uv run photobook-generator build ./photos-root --manifest   # default: ./photos-root/ingest.yml
```

A root `ingest.yml` declares which subfolders are album boundaries and which are
segment boundaries, by relative path:

```yaml
albums:
  - path: lisbon-2026            # → its own Immich album + album.yml + run + book
    title: Lisbon 2026           # optional; else the folder name, titleized
    timezone: Europe/Podgorica  # optional; else the --timezone default
    segments:                   # optional; each subfolder → one Segment,
      - path: day-1-harbour     #   its time window = [min, max] of that
      - path: day-2/old-town    #   subfolder's photo capture timestamps
        place: Old Town         # optional (no geocoding)
  - path: wedding
    source_document: runsheet.pdf   # PDF-derived segments instead of folders
```

Fan-out is **opt-in** (a stray `ingest.yml` never changes plain `build <dir>`),
and per-album fields live in the manifest — `--slug`/`--title`/`--source-document`/
`--lead-face` are rejected in manifest mode (`--timezone`/`--captioner`/`--force`
still apply to every album). A bad album (empty folder, undated photos, a broken
derived `album.yml`) is isolated and skipped; the run continues and prints a combined
books-written / albums-failed summary. Same caveat as above: the stack must be up and
`IMMICH__API_KEY` set. See
[folder-structure-driven ingestion](./knowledge/build/folder-structure-ingestion.md).

### Scan photos in place (no second copy)

By default `build` **uploads** a copy of every photo into Immich, so a 10 GB
folder costs ~10 GB again in Immich's store. `--ingest-mode external` instead
registers the folder as an Immich **external library** — a read-only bind mount
Immich scans *in place*, no copy:

```sh
uv run photobook-generator build ./photos --ingest-mode external
```

It is **opt-in** (upload stays the default) and a run-level mode — it applies to
every album, including `--manifest` fan-out. Setup, in `.env`:

- Uncomment the `${PHOTOS_LOCATION}:/external:ro` mount in
  `docker-compose.immich.yml` and set `PHOTOS_LOCATION` to your host photo root.
- Set `IMMICH__EXTERNAL_HOST_ROOT` to that same host root and (if you changed the
  mount target) `IMMICH__EXTERNAL_CONTAINER_ROOT` (default `/external`).
- `IMMICH__API_KEY` must be the **admin's** key (external libraries are admin-owned).
- The default can also be set once via `IMMICH__INGEST_MODE=external`; the flag wins.

**You still pass `<dir>` — it's the album, not the mount.** These two paths do
different jobs. `PHOTOS_LOCATION` / `IMMICH__EXTERNAL_HOST_ROOT` is *deployment*
config: the whole tree Immich mounts and scans, served by **one shared library
reused across every run**. `build <dir>` is a *per-run* choice — **which** photos
under that root make up this album (collected from `<dir>`, then matched against
the scanned library by path). `<dir>` must sit **under** the mount root. If it
equals the root, your whole folder is one album; point it at a subfolder (or use
`--manifest`) to carve the tree into several. The `<dir>` argument means the exact
same thing in upload mode — external mode only changes *how* those photos reach
Immich, never *which* photos are the album.

The trade-off: the originals must **stay mounted** for the life of the album —
if the mount goes away Immich marks the assets offline and later stages that read
originals (thumbnails, scoring, faces, render) break. See
[external-library ingestion](./knowledge/build/external-library-ingestion.md).

## Examples

Once the stack is up and `IMMICH__API_KEY` is set (see
[Quick start path](#quick-start-path) for that one-time setup), a single `build`
connects a directory to Immich — creating the album and uploading (or scanning)
its photos — and then runs the full pipeline to generate the book.

### One folder → one book

The base case: point `build` at a directory. It uploads a copy of each photo
into a new Immich album, then runs every stage.

```sh
uv run photobook-generator build ./honeymoon
```

### Name the album and set a timezone

Override the derived slug/title, and give undated photos an IANA timezone so
they sort correctly.

```sh
uv run photobook-generator build ./honeymoon \
  --slug honeymoon-2026 --title "Honeymoon 2026" --timezone Europe/Podgorica
```

### Derive chapters from a schedule

Extract the album's `segments` (chapters) from an itinerary, run-sheet, or
line-up PDF instead of hand-authoring them.

```sh
uv run photobook-generator build ./wedding --source-document runsheet.pdf
```

### AI-written captions

Swap the default offline template captions for Claude-generated ones. Needs
`ANTHROPIC_API_KEY` in `.env`.

```sh
uv run photobook-generator build ./honeymoon --captioner claude
```

### Scan photos in place (no second copy)

Register the folder as a read-only Immich external library instead of copying
every file in — see [Scan photos in place](#scan-photos-in-place-no-second-copy)
for the required mount setup.

```sh
uv run photobook-generator build ./photos --ingest-mode external
```

### A tree of albums → N books

Fan a directory that holds several albums into N independent books in one run,
driven by a root `ingest.yml` manifest.

```sh
uv run photobook-generator build ./photos-root --manifest
```

### Re-run an existing album

Skip the ingest phase entirely and (re)run the pipeline on an album that already
has an `album.yml` — handy for A/B-ing a different `--captioner`.

```sh
uv run photobook-generator build --no-ingest --album honeymoon-2026
```

## Run in Docker

The `app` image bundles the native tools the pipeline shells out to — notably
`darktable-cli` for [`render`](./knowledge/stages/render.md) — so running inside
it needs nothing installed on your host. This is the intended way to run the full
pipeline, and the only option if your OS can't install darktable. Bring the stack
up first:

```sh
uv run poe up
```

Then run `build` in a one-off `app` container. The one required override is
`IMMICH__BASE_URL`: inside the container `localhost` is the container itself, so
point it at Immich's network name (`DATABASE_URL` / `PREFECT_API_URL` are already
overridden for you in `docker-compose.yml`).

**Resume or re-run an already-ingested album** — no photo mount needed, since
`render` fetches originals from Immich:

```sh
docker compose run --rm \
  -e IMMICH__BASE_URL=http://immich-server:2283/api \
  app build --no-ingest --album <slug>
```

**Fresh run from a directory** (ingest + generate) — ingest reads the folder, so
mount it into the container at the *same* host path:

```sh
docker compose run --rm \
  -e IMMICH__BASE_URL=http://immich-server:2283/api \
  -v /path/to/photos:/path/to/photos:ro \
  app build /path/to/photos --ingest-mode external
```

`--rm` discards the one-off container; everything after `app` is passed to the
`photobook-generator` entry point. The book lands at
`data/albums/<slug>/book/index.html` on your host (`./data/albums` is
bind-mounted into the container).

> **Note:** the image ships the **offline** pipeline — aesthetic scoring falls
> back to NULL (degraded curation), since the heavy `scoring` extra
> (`torch` + `open_clip`) is deliberately left out to keep the image lean.

## Architecture

Batch pipeline over Immich + Postgres, orchestrated by Prefect, PydanticAI for
the (optional) LLM call — fully offline by default.

→ [`knowledge/foundations/architecture.md`](./knowledge/foundations/architecture.md)
→ [`knowledge/foundations/domain-model.md`](./knowledge/foundations/domain-model.md)
→ [`knowledge/foundations/pipeline-execution.md`](./knowledge/foundations/pipeline-execution.md)
(companion diagram: `pipeline-execution.html`)

## Pipeline stages

Ten stages, `sync` → `fulfill`, one concept per stage.

→ [`knowledge/stages/index.md`](./knowledge/stages/index.md)

## Reference

Album definition, data model, tech stack, folder structure.

→ [`knowledge/reference/album-definition.md`](./knowledge/reference/album-definition.md)
→ [`knowledge/reference/data-model.md`](./knowledge/reference/data-model.md)
→ [`knowledge/reference/tech-stack.md`](./knowledge/reference/tech-stack.md)
→ [`knowledge/reference/folder-structure.md`](./knowledge/reference/folder-structure.md)

## Process & workflow commands

TDD, typing, the git commit gate, the per-step implementation loop, and the
full `poe` task table.

→ [`knowledge/process/working-agreement.md`](./knowledge/process/working-agreement.md)
→ [`knowledge/process/implementation-workflow.md`](./knowledge/process/implementation-workflow.md)
→ [`knowledge/process/quality-practices.md`](./knowledge/process/quality-practices.md)

## Build status

What's done, what's deferred, and why.

→ [`knowledge/build/progress.md`](./knowledge/build/progress.md)
→ [`knowledge/build/build-order.md`](./knowledge/build/build-order.md)

## Ordering a physical copy

The default `fulfill` stage writes a self-contained HTML book (`HtmlPrinter`).
To order a physical copy via [Lulu](https://api.lulu.com)'s Print API instead,
inject `LuluPrinter` and set the `LULU__*` keys in
[`.env.example`](./.env.example).

→ [`knowledge/stages/fulfill.md`](./knowledge/stages/fulfill.md)

## Contributing

`main` is protected by a `no-commit-to-branch` hook — work always happens on a
`step-NN-<slug>` branch, TDD, reviewed, squashed, and merged.

→ [`AGENTS.md`](./AGENTS.md)
→ [`knowledge/process/implementation-workflow.md`](./knowledge/process/implementation-workflow.md)

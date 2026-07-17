# Photo Album Pipeline

A batch pipeline that turns a raw dump of photos into a curated, enhanced,
captioned, print-ready photo book, submitted to a print API — with minimal
manual work. It's **event-type agnostic**: a trip, a wedding, a baby's first
year, a festival are all just *albums* (an `Album` with optional, named
`Segment`s).

The [`knowledge/`](./knowledge/index.md) directory is this project's source of
truth — domain model, architecture, data model, and the step-by-step build
order. [`AGENTS.md`](./AGENTS.md) orients coding agents working in this repo.

## From a folder of photos to a finished album

You have a folder of photos on your disk. You want a browsable HTML photo book.
Here is every step, in order. Do **Point 0** once; do the rest each time you
make an album.

Photos don't go into the pipeline directly — they first live in **Immich** (a
self-hosted photo library that runs locally in Docker and does the EXIF, face,
and duplicate detection for us). The pipeline then reads one Immich *album* and
turns it into a book. So the flow is: **your folder → Immich album → pipeline →
`index.html`.**

### Point 0 — Install (once)

Install the two things the project needs, then set it up:

- [`uv`](https://docs.astral.sh/uv/) — runs Python for you (no separate Python
  install; it targets Python 3.14).
- [Docker](https://docs.docker.com/get-docker/) — runs the local services
  (Immich, two Postgres databases, Prefect).

```sh
uv sync                    # creates .venv/ and installs dependencies
uv run pre-commit install  # installs the git hooks (ruff, basedpyright, etc.)
cp .env.example .env       # your local settings + secrets live here
```

Always run project tools through `uv run ...` (or activate `.venv/` first) so
they resolve against the project's virtualenv, not a system Python.

### Point 1 — Start the local services

```sh
uv run poe up       # docker compose up: Immich + Prefect + both Postgres DBs
uv run poe migrate  # create our app's database tables (alembic upgrade head)
```

Give Immich a minute the first time — it pulls several container images. When
it's up, its web UI is at `http://localhost:2283`.

### Point 2 — Put your photos into Immich

1. Open `http://localhost:2283` and create the admin account (first visit only).
2. Create an album and upload your folder into it — drag-and-drop the whole
   folder in the web UI, or use the official
   [Immich CLI](https://docs.immich.app/features/command-line-interface)
   (`immich upload --album "<name>" /path/to/your/photos`) for large folders.
3. Let Immich finish its background jobs (EXIF extraction, plus face and
   duplicate detection). GPS is used when present but never required.

### Point 3 — Collect two keys and one id

Paste these into your `.env` (see [`.env.example`](./.env.example) for the exact
variable names):

- **Immich API key** → `IMMICH__API_KEY`. In Immich: *Account Settings → API
  Keys → New*.
- **Anthropic API key** → `ANTHROPIC_API_KEY`. Used for captions and the
  enhance stage. Get one at `https://console.anthropic.com`.
- **Immich album id** — open your album in Immich; it's the last part of the
  URL (`.../albums/<this-part>`). You'll paste it into `album.yml` next.

*(The `LULU__*` keys are only for ordering a physical book — see
[Order a physical copy](#order-a-physical-copy). Skip them for an HTML book.)*

### Point 4 — Describe your album

Create `data/albums/<slug>/album.yml`. The minimum is a slug, a title, and the
Immich album id from Point 3:

```yaml
# data/albums/my-trip/album.yml
slug: my-trip
title: My Trip
immich_album_id: 8f2c...        # from Point 3
timezone: Europe/Warsaw         # used to interpret photo times lacking an offset
```

That's the whole album definition — no code, no database editing. Optional
`segments` (chapters by time/place) and `lead_faces` (priority people) are
documented in [Album definition](./knowledge/reference/album-definition.md).

### Point 5 — Run the pipeline

```sh
uv run album run --album my-trip
```

This runs every stage end-to-end: pull the album from Immich → score and
de-duplicate → group into chapters → pick the best shots → auto-enhance →
caption → lay out pages → write the book. Watch it live in Prefect's UI at
`http://localhost:4200`.

### Point 6 — Open your book

The book lands at:

```
data/albums/my-trip/book/index.html
```

Open that file in any browser to page through it — captions, chapter dividers,
and all your photos, in one self-contained page. Done.

---

## How the book gets written

The final `fulfill` stage delivers the assembled book through a pluggable
[`Printer`](./src/album/printers/base.py). The **default** is `HtmlPrinter`: it
writes a single self-contained, browsable webpage to
`data/albums/<slug>/book/index.html` — a page-by-page viewer with captions and
segment dividers, rendering the on-disk photos, no external service needed. Open
that file in a browser to page through the book.

### Order a physical copy

To order a physical copy instead, inject the opt-in `LuluPrinter`
(`pipeline(..., printer=LuluPrinter.from_settings(LuluClient.from_settings()))`),
which submits the print-ready PDF to [Lulu](https://api.lulu.com)'s Print API —
this needs the `LULU__*` credentials and a PDF hosted at a URL Lulu can fetch.

Every dependency `flow/_flow.py` wires is real except one, deliberately deferred
since [build step 6](./knowledge/build/progress.md): the LAION-style aesthetic
predictor + CLIP embedder (`open_clip`/`torch` — a heavy, not-yet-added
dependency). A run still completes without it — `select`'s MMR has a
documented fallback for missing `aesthetic_score`/`clip_embedding` — just
with degraded curation quality until that's wired.

## Workflow commands

| Command | What it does |
|---|---|
| `uv run poe lint` | `ruff check .` |
| `uv run poe format` | `ruff format .` (write) |
| `uv run poe typecheck` | `basedpyright` |
| `uv run poe test` | `pytest` |
| `uv run poe check` | lint + `ruff format --check .` + typecheck + test — the commit gate |
| `uv run poe up` / `uv run poe down` | bring the local stack up / down |
| `uv run poe migrate` | `alembic upgrade head` |
| `uv run poe run -- --album <slug>` | run the pipeline flow for one album |

The pipeline is also a console entry point: `uv run album run --album <slug>`.

## Contributing

See [`AGENTS.md`](./AGENTS.md) and
[`knowledge/process/implementation-workflow.md`](./knowledge/process/implementation-workflow.md)
for how a build step is branched, developed test-first, reviewed, and merged.
`main` is protected by a `no-commit-to-branch` pre-commit hook — work always
happens on a `step-NN-<slug>` branch.

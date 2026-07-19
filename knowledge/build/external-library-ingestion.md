---
type: Playbook
title: "External-library (scan-in-place) ingestion — reference photos on disk instead of copying them into Immich"
description: A switchable ingest mode that registers the photo directory as an Immich External Library (a read-only bind mount scanned in place) instead of uploading every file, so a multi-GB dump needs no second copy. Same manifest, same downstream pipeline; the only change is how {path → asset id} is obtained. Off by default (upload mode); enabled per run by config (IMMICH__INGEST_MODE) or a --ingest-mode flag.
tags: [playbook, ingest, immich, external-library, scan-in-place, no-copy, config, cli, docker, planned]
timestamp: 2026-07-19
status: planned
---

# External-library (scan-in-place) ingestion

Follow-on to the [single-command](/build/archive/single-command-ingestion.md) and
[folder-structure](/build/archive/folder-structure-ingestion.md) ingestion playbooks.
Both upload every photo to Immich via `upload_asset` (`POST /assets`), so Immich
stores its **own copy** of the whole dump — a 10 GB album costs ~10 GB again in
Immich's storage. The folder-structure playbook explicitly kept this decoupled from
External Libraries and flagged scan-in-place as *future work*
([decision 7](/build/archive/folder-structure-ingestion.md), "Build against today's
upload path; stay decoupled from External Libraries"). **This playbook is that future
work.**

## What it does

Adds a second ingest mode that costs **no extra disk**: the photo directory is
bind-mounted read-only into the Immich server container and registered as an Immich
**External Library**. Immich scans the files *in place* and creates assets that point
at the on-disk originals — no copy into its upload store.

```sh
# today (unchanged default): upload a copy into Immich
photobook-generator build <dir>

# new: reference the files on disk, no copy
photobook-generator build <dir> --ingest-mode external
IMMICH__INGEST_MODE=external photobook-generator build <root> --manifest
```

The switch works for **both** the flat and the `--manifest` fan-out paths, because it
sits at a seam shared by both.

## The seam (why this is small and safe)

`DirectoryIngestor._ingest` (`src/album/ingest.py`) does, for every album:

```python
image_paths = self._collect_images(directory)
path_to_asset_id = {path: self._client.upload_asset(path) for path in image_paths}
immich_album_id = self._client.create_album(title, list(path_to_asset_id.values()))
# ... everything after this only reads path_to_asset_id and immich_album_id
```

Downstream — folder/PDF segment windows, `album.yml` materialization, the whole
pipeline — consumes **only** `path_to_asset_id` and `immich_album_id`. It never cares
*how* the assets got into Immich. So the entire feature is: swap the one
`{path: upload_asset(path)}` line for a pluggable strategy that returns the same
`dict[Path, str]`. `create_album` and everything after stay identical.

Introduce an **`AssetRegistrar`** protocol with one method:

```python
class AssetRegistrar(Protocol):
    def register(self, directory: Path, image_paths: list[Path]) -> dict[Path, str]:
        """Ensure each photo is an Immich asset; return {host path → asset id}."""
```

- **`UploadRegistrar`** — today's behavior, extracted verbatim (`upload_asset` per
  file). Default.
- **`ExternalLibraryRegistrar`** — ensure the library exists, scan, wait, and map each
  host path to the scanned asset id by matching `originalPath`.

`DirectoryIngestor` takes an `AssetRegistrar` (defaulting to `UploadRegistrar`);
`ingest_directory` / `ingest_manifest` build it from settings. That is the only change
to the ingestor; both flat and manifest paths inherit it for free.

## How external mode works, step by step

1. **Bind-mount** the photo root read-only into the Immich server container (Docker;
   see below). Immich sees it at a fixed *container* path (e.g. `/external`).
2. **Ensure the library** exists: create an External Library whose `importPaths`
   contains the container path, idempotently (reuse by name/import path across runs and
   across a manifest's N albums — it is one library, not one per album).
3. **Scan** (`POST /libraries/{id}/scan`) and **wait** for the scan job to drain — the
   readiness wait must now cover the library-scan queue, not only EXIF (extend the
   existing `_wait_for_readiness` job poll, `get_jobs`).
4. **Map** each host image path to its asset id by listing the library's assets and
   matching on `originalPath` (translated host↔container path — see below). Filter to
   *this* album's `image_paths`, since one library may span the whole manifest tree.
5. Return `{host Path → asset id}`. From here `_ingest` is unchanged.

## Path translation (the one genuinely fiddly part)

The CLI/manifest speak **host** paths (`/home/devel/photos/lisbon/img_1.jpg`); Immich's
`originalPath` is a **container** path (`/external/lisbon/img_1.jpg`). The registrar
translates between them using a configured root pair:

- `IMMICH__EXTERNAL_HOST_ROOT` — host prefix of the mounted tree (`/home/devel/photos`)
- `IMMICH__EXTERNAL_CONTAINER_ROOT` — where it is mounted in the container (`/external`)

`container = container_root / host.relative_to(host_root)` and back. Fail fast with a
clear error if a photo lies outside `host_root` (it can't be scanned) — this is the
most likely user misconfiguration and deserves a good message.

## Config / flag (the switch)

Add to `ImmichSettings` (`src/album/config.py`):

```python
ingest_mode: str = "upload"          # "upload" | "external"; IMMICH__INGEST_MODE
external_host_root: str = ""         # IMMICH__EXTERNAL_HOST_ROOT
external_container_root: str = "/external"   # IMMICH__EXTERNAL_CONTAINER_ROOT
```

- **Env default**: `IMMICH__INGEST_MODE=external` in `.env`.
- **Per-run override**: a `--ingest-mode {upload,external}` CLI flag on `generate`
  (shared helper like `_add_captioner_arg`), flag wins over env, like `--captioner`.
- Validate the value where the registrar is built (mirrors the `captioner`/`scorer`
  pattern of keeping the string→object mapping out of `config.py`). In external mode,
  require `external_host_root` non-empty and that the directory is under it — else a
  clean `IngestError`.

## Docker

`docker-compose.immich.yml` — add a read-only bind mount and env pointer:

```yaml
services:
  immich-server:
    volumes:
      - ${UPLOAD_LOCATION}:/data
      - ${PHOTOS_LOCATION}:/external:ro   # NEW: originals, scanned in place, never written
```

Document `PHOTOS_LOCATION` (host photo root) in `.env.example`, and that it must equal
`IMMICH__EXTERNAL_HOST_ROOT` mapping to `IMMICH__EXTERNAL_CONTAINER_ROOT` (`/external`).
Only `immich-server` needs the mount (it runs the scan and serves originals).

## Decisions (confirmed with the user 2026-07-19, before step 36)

1. **Default stays `upload`. [CONFIRMED]** External is opt-in — it needs a bind mount
   and admin API key, so it must not surprise the existing flow.
2. **One shared library, reused across runs and manifest albums [CONFIRMED]**
   (idempotent by name/import path), not one per album. Album membership is still
   expressed by our Immich *albums* (`create_album`), which external assets join
   normally.
3. **Originals must stay mounted for the life of the album. [CONFIRMED]** If the mount
   goes away, Immich marks the assets *offline* and later stages that fetch originals
   (thumbnails, CLIP scoring, faces, the final render) break. This is the accepted cost
   of not copying — it is the core trade-off the feature exists to make.
4. **No content-dedupe in external mode. [CONFIRMED]** Upload mode dedupes by bytes;
   external assets are deduped by path. Re-scanning the same tree is idempotent, but two
   identical files at two paths become two assets — accepted.

## Risks / landmines

- **Immich API drift.** External-Library endpoints and DTOs have changed across Immich
  releases. **Verify the exact `/libraries` create/scan/list shape against the pinned
  `IMMICH_VERSION`** before writing the client methods — do not code from memory.
- **Admin ownership.** External libraries belong to the admin user; `IMMICH__API_KEY`
  must be the admin's key. Add a preflight check with a clear error.
- **Scan is async and library-wide**, not per-album. Wait on the library-scan job
  queue, and always *filter* the returned assets down to the album's `image_paths`.
- **Readiness wait** must be extended to include the scan (step 3) — otherwise the
  path→id map is built before assets exist and comes back empty.

## Build steps (proposed)

TDD throughout, against the fake `IngestClient` the ingestor tests already use;
live/integration verification folds into the existing "no reachable live Immich"
blocker (needs a real Immich with a bind mount — a TestContainers variant can cover the
happy path offline).

- **Step 36 — plumbing.** `ImmichSettings` fields; host↔container path translation
  (`PathMapping`, pure, unit-tested); `ImmichClient` library methods
  (`get_current_user_id`/`get_libraries`/`create_library`/`scan_library`/
  `get_library_assets`) + `originalPath` on `ImmichAsset`, modelled on the documented
  Immich API; Docker mount + `.env.example`; reference-doc section. *(The
  `_wait_for_readiness` scan-queue extension moved to step 37 — the queue to wait on is
  a property of the registrar that triggers the scan, so it belongs with the seam.)*
- **Step 37 — the seam.** `AssetRegistrar` protocol; extract `UploadRegistrar` from the
  current inline upload (pure refactor, behavior identical, tests stay green);
  `ExternalLibraryRegistrar` (ensure-library → scan → wait-on-`library`-queue → map by
  `originalPath`); extend `_wait_for_readiness` so the strategy declares its queues;
  inject the registrar into `DirectoryIngestor` (default upload) and build it from
  settings in `ingest_directory`/`ingest_manifest`.
- **Step 38 — CLI + docs + verify.** `--ingest-mode` flag (flag > env, validated);
  README / ingest stage docs; end-to-end genericity gate; run `/verify` and the
  live-run checklist entry.

## Genericity gate

Nothing here mentions a trip/wedding/occasion. The manifest, the CLI, and all new code
speak only "directory / photo / asset / library / path". The feature changes *how bytes
reach Immich*, never *what the album is about* — genericity holds trivially.

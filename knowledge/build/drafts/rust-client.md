---
type: Design Brief
title: Monorepo split + client-side WASM prototype (rust-client)
description: Move the whole Python pipeline into pipeline/ so the repo becomes a monorepo, then build a greenfield /rust-client spike (Svelte + shadcn-svelte shell, Rust/WASM wgpu photo viewer, Rust/WASM core over sqlite-vec, transformers.js CLIP) to test how much of the analysis/select stack can run client-side in the browser.
tags: [monorepo, rust, wasm, webgpu, svelte, shadcn, sqlite-vec, clip, transformers-js, client-side, spike, exploration]
timestamp: 2026-07-24
---

# Monorepo split + client-side WASM prototype (rust-client)

> **Status: draft.** A design brief for a research spike + the repo restructure it
> needs, not scheduled build work. Extends
> [ML boundary & quality-signal exploration](ml-and-quality-exploration.md).
> Promote items into `progress.md`'s Outstanding table if/when we pursue this for real.

## Context

The pipeline runs entirely server-side in Python: `open_clip` ViT-L/14 produces
768-d CLIP embeddings stored as a **pgvector** column (`src/album/db/models.py:139`),
with similarity computed in-database (`src/album/db/similarity.py`,
`PgVectorSimilarity`). We've been exploring how much of the analysis/select stack
(embeddings, face detection, aesthetic scoring, dedup/selection, basic
enhancement, captioning) could run **client-side in the browser** via WASM +
WebGPU, and whether a non-JS (Rust) core is worth it.

Two changes, in order:

- **Part A — Monorepo split.** Move the entire current Python project into
  `pipeline/` so the repo root can host a second, non-Python project as a sibling.
  Frames the two as generations of one product (old server pipeline / new client).
- **Part B — rust-client spike.** The smallest end-to-end slice that answers with
  running code: *can a JS shell host a Rust/WASM component that owns the pixels (a
  GPU photo viewer) while a second Rust/WASM module owns the data (vector search
  over sqlite-vec), with the ML model running through transformers.js?* It touches
  all three concerns — visual (Rust/GPU), model (JS), data (Rust) — so we can feel
  out the language boundary and the browser's real limits before committing.

**Outcome:** a working monorepo layout + a throwaway-quality but genuinely working
`/rust-client` app, plus written findings on feasibility, DX, and the boundary.
Not production; the client is not wired into the Python pipeline or its DB.

---

## Part A — Monorepo split

### Target layout

```
photobook-transformer/            (repo root — monorepo)
  pipeline/          ← the entire current Python project, moved wholesale
    src/album/  scripts/  tests/  alembic/  alembic.ini  templates/  data/
    pyproject.toml  uv.lock  .python-version
    Dockerfile  .dockerignore  docker-compose.yml  docker-compose.immich.yml
    .env  .env.example  README.md  README.html
  rust-client/       ← the new Svelte + Rust/WASM client (Part B)
  knowledge/         ← shared docs bundle (stays at root — single source of truth)
  AGENTS.md  CLAUDE.md  .pre-commit-config.yaml  .gitignore   ← root, monorepo-level
  README.md          ← new slim monorepo readme (points to pipeline/ + rust-client/)
```

### Why paths mostly survive
Relative paths are anchored to their config files, so moving as a unit keeps them
valid **when tooling is invoked from `pipeline/`**:
- `alembic.ini` uses `%(here)s/alembic` → self-relative.
- `pyproject.toml` (`basedpyright include=["src","tests","scripts"]`, poe
  `preview = "python scripts/preview_gallery.py"`) → relative to pyproject.
- `docker-compose.yml` `build: .` + `./data/albums` volumes + `.env` → consistent
  because `data/` and `.env` move too.

### Manual fixes required
- **`.gitignore`** (stays at root): prefix anchored rules with `pipeline/` —
  `pipeline/data/albums/*` (+ its `!` negations), `pipeline/data/immich/`,
  `pipeline/tests/data/cat-album/...`. Unanchored rules (`.venv/`, `*.pyc`,
  `.env`, `.cache/`, `.DS_Store`) already match at any depth — leave them. Add
  `rust-client/` ignores (`node_modules/`, `dist/`, Rust `target/`, `pkg/`).
- **`.pre-commit-config.yaml`** (stays at root — git hooks live at repo root):
  the `local` hooks must run inside the project. Change
  `entry: uv run basedpyright` → `entry: bash -c 'cd pipeline && uv run basedpyright'`
  (same for `xenon ... src` and `poe check`). Root-repo hooks
  (`no-commit-to-branch`, `ruff`, `ruff-format`) are fine — ruff resolves
  per-file config by walking up to `pipeline/pyproject.toml`.
- **`.venv`**: do **not** move it (absolute shebangs). Delete the old root `.venv`
  and recreate with `uv sync` run from `pipeline/` (wheels are uv-cached, so this
  relinks rather than re-downloads torch).
- **`AGENTS.md` / `CLAUDE.md`** (stay at root): add a short "monorepo layout"
  note — all pipeline commands (`uv run poe ...`, `photobook-generator build`)
  now run from `pipeline/`. Keep the OKF `knowledge/` bundle as source of truth.
- **New root `README.md`**: one screen — what the two subprojects are, where to
  start. Move the current pipeline README into `pipeline/README.md`.

### Preserve WIP
There is an **uncommitted local edit to `scripts/preview_gallery.py`** at the time
of writing — `git mv` preserves working-tree modifications, so it rides along;
verify it survives the move.

### Execution (all `git mv` to preserve history)
1. Branch `spike-rust-client` off `main` (the `no-commit-to-branch` hook forbids
   committing to `main`).
2. `mkdir pipeline`; `git mv` each project item listed in the layout into `pipeline/`.
3. Apply the manual fixes above; add slim root `README.md`.
4. `rm -rf .venv .ruff_cache .pytest_cache` (orphaned root caches); `cd pipeline && uv sync`.

### Verify Part A (before touching Part B)
- `cd pipeline && uv run python -c "import album"` — imports from new location.
- `cd pipeline && uv run ruff check .` and `uv run basedpyright` — static gate green.
- `cd pipeline && uv run alembic history >/dev/null` — alembic config resolves.
- `git status` shows moves as renames (history preserved) and the
  `preview_gallery.py` edit still present.
- Full `poe check` (integration pytest) needs Docker/Postgres up — run if services
  are available; otherwise the static + import checks above are the gate for the move.

---

## Part B — rust-client spike

### Architecture — polyglot components (JS shell + Rust islands)

```
Svelte + shadcn-svelte shell (Vite + TS)      ← chrome, gallery grid, controls, state
  ├─ <photo-viewer> ─────► Rust/WASM + wgpu    ← owns a <canvas>: GPU pan/zoom + exposure shader
  ├─ transformers.js (WebGPU) ──► CLIP         ← image → embedding (JS, best-supported model stack)
  └─ core (Rust/WASM) ──► sqlite-vec           ← store embedding, KNN "find similar" (exact)
```
Boundary: `wasm-bindgen` exports Rust fns the shell calls as TS; the shell hands
the viewer a `<canvas>` that Rust renders into via wgpu (no per-frame copy); the
exposure slider (shadcn `Slider`) passes a float into Rust each frame.

### Folder structure
```
/rust-client
  README.md  package.json  vite.config.ts  index.html
  /src                      # Svelte shell
    App.svelte
    lib/ viewer.ts clip.ts store.ts components/ui/...   # shadcn-svelte components
  /viewer                   # Rust → WASM (wgpu photo viewer): init canvas, texture, set_exposure()
  /core                     # Rust → WASM (data): insert_embedding(), knn(query,k) over sqlite-vec
```

### Steps
1. **Shell scaffold**: Vite + Svelte + TS; init shadcn-svelte (Tailwind + CSS-var
   theming, light/dark). Static layout: gallery grid (drop zone) + viewer pane +
   exposure `Slider` + "Find similar" button. No WASM yet.
2. **CLIP via transformers.js** (`lib/clip.ts`): load CLIP on WebGPU (default
   `Xenova/clip-vit-base-patch32`, 512-d; toggle to ViT-L/14 → 768-d for parity
   with `db/models.py:139`). Drop image → embedding → log. Cache weights.
3. **Rust `core` → WASM** (sqlite-vec): `wasm-pack build --target web`. Exports
   `insert_embedding(id, Float32Array)` + `knn(Float32Array, k) -> ids`. Wire
   `store.ts`; "Find similar" highlights nearest thumbnails. Proves JS→Rust→data.
4. **Rust `viewer` → WASM** (wgpu): shell hands the photo's `<canvas>` to Rust;
   upload texture, render; `set_exposure(f32)` fragment-shader multiply driven by
   the shadcn `Slider`, live at 60fps. Proves JS-shell ↔ Rust-GPU boundary.
5. **Wire end-to-end**; write findings back into this doc (download sizes,
   first-load vs warm, WebGPU availability, memory feel, wasm-bindgen DX,
   sqlite-vec-in-browser vs server pgvector).

### Toolchain to add (not yet installed)
`wasm-pack` (`cargo install wasm-pack`) and the `wasm32-unknown-unknown` target
(`rustup target add wasm32-unknown-unknown`). node/npm/cargo/rustup already present.

### Isolation (non-negotiable)
`/rust-client` is the first non-Python stack; it must **not** enter `uv run poe
check` (ruff/basedpyright/xenon/pytest assume Python). It carries its own
README + build steps and toolchain. `poe check` stays green by construction.

### Key risks
- **R1 — wgpu-in-WASM is the hard part** (WebGPU-only: Chrome/Edge or Safari 18+;
  no WebGL fallback). Do steps 1–3 first so there's a working slice even if 4
  slips; fallback is a Canvas2D exposure op (documented, not preferred).
- **R2 — sqlite-vec in a WASM build.** Cleanest: Rust SQLite binding bundling
  SQLite with the statically-compiled `sqlite-vec` extension. Fallback: `core`
  stores raw Float32 + a hand-written cosine KNN in Rust (no extension) — still
  proves data-in-Rust; note which path was taken.
- **R3 — Embedding dim.** Spike default 512; toggle to 768 for pgvector parity.
- **R4 — wasm threads/SIMD** need COOP/COEP headers (set in `vite.config.ts`);
  nice-to-have, not required for the slice.

### Verify Part B
`cd rust-client && npm install && npm run dev`:
- Step 2: drop a photo → console shows a 512-d embedding; re-drop is instant (cached).
- Step 3: drop several → click a thumbnail → "Find similar" highlights nearest;
  near-duplicates rank top (exact KNN).
- Step 4: select a photo → renders in the viewer canvas; dragging exposure
  brightens/darkens live, no visible lag.
- Confirm WebGPU active (`navigator.gpu`); note behavior when absent.

## Progress & findings (2026-07-24)

**Part A — monorepo split: done & verified.** Whole Python project moved to
`pipeline/` (207 renames, history preserved; the uncommitted `preview_gallery.py`
WIP survived). `.gitignore` / pre-commit / AGENTS.md / CLAUDE.md / root README
updated; venv rebuilt via `uv sync`. Verified: `import album`, `ruff check`,
`basedpyright` (0 errors), `alembic history` all green from `pipeline/`.

**Part B — steps 1–3: done & verified in-browser** (Chrome, WebGPU present).
Vite + Svelte 5 + Tailwind v4 + shadcn-style CSS-var theme; `core` crate built with
`wasm-pack`. Dropped 6 test photos → CLIP embedded them on **WebGPU** (512-d) →
inserted into the Rust `VecStore` → **"Find similar" returned correct nearest
neighbours** (the duplicate ranked top at cosine ≈ 1.0, green outlines) → Canvas2D
viewer showed the selected photo with a **live exposure slider** (1.2 EV visibly
brighter). Type gate (`npm run check`) and prod `build` both clean.

Findings so far:
- **The JS-shell ↔ Rust-WASM boundary is a non-event.** `wasm-bindgen` exports call
  like plain TS; `Float32Array` ⇄ `&[f32]` and `Vec<u32>` ⇄ `Uint32Array` marshal
  transparently. The `core` crate is ~80 lines.
- **transformers.js on WebGPU "just works"** for CLIP image embeddings; first load
  downloads the model (cached after). The prod bundle pulls in a ~23 MB ONNX-runtime
  WASM (the CPU fallback backend) — worth code-splitting later.
- **Two footguns cost time, both documented:** (1) wasm-pack writes a `*`-only
  `.gitignore` into its out-dir, which **svelte-check honors** → the generated
  `.d.ts` becomes invisible and imports fail to resolve; `npm run wasm` deletes it.
  (2) Svelte 5 `$state` deep reactivity: mutate the **proxied** array element
  (`arr[i].x = …`), not the plain object literal you pushed.
- **R2 confirmed:** went with the Rust exact-KNN fallback rather than sqlite-vec.

**Part B — step 4 (Rust/wgpu viewer): done & verified in-browser.** The `viewer`
crate (wgpu 23) creates a WebGPU surface directly on the shell's `<canvas>`, uploads
the selected photo as an sRGB texture, and renders a contain-fit quad; exposure is a
live WGSL fragment-shader gain driven by the shadcn slider. Confirmed live: "viewer:
wgpu", the photo renders GPU-side with letterbox bars, and the slider brightens it in
real time (1.05 EV) — all three legs (CLIP → Rust KNN → Rust/wgpu pixels) working
together. R1 is therefore resolved: the JS-shell ↔ Rust-GPU-canvas boundary works via
`SurfaceTarget::Canvas` with no per-frame copy.

wgpu findings:
- **wgpu must be ≥ 23**: v22 requests the `maxInterStageShaderComponents` limit that
  current Chrome removed from the WebGPU spec → `requestDevice` rejects it.
- **Canvas context is single-use**: a failed wgpu attempt leaves a `webgpu` context
  on the canvas, so the Canvas2D fallback's `getContext('2d')` returns null — the
  fallback had to be made null-tolerant rather than throwing (it was crashing onMount).
- The whole viewer crate is ~300 lines incl. the WGSL; wgpu-in-wasm was the only
  genuinely fiddly part of the spike, and only at the version/limits layer.

**Spike status: complete.** All of Part A + Part B (steps 1–4) done and verified.

## Follow-on robustness (post-spike, verified)

- **One viewer, WebGPU + WebGL2.** The `viewer` crate enables wgpu's `webgl` feature
  and requests `BROWSER_WEBGPU | GL`; naga transpiles the WGSL to GLSL, so the *same*
  Rust viewer runs on WebGPU (preferred) or WebGL2 (fallback) — verified both live
  (forced GL-only build rendered + exposure worked, `viewer: webgl2`). Canvas2D is now
  only a last resort. This collapses the earlier dual GPU/CPU viewer into one codebase.
- **Graceful GPU degradation + theme-aware background.** CLIP picks `webgpu`/`wasm`
  from a one-shot `requestAdapter()` probe; the wgpu clear colour is set from the
  page's card colour (getComputedStyle → 1×1 canvas rasterize → sRGB → linear) so the
  letterbox matches light/dark, restoring the Canvas2D look.

## Client-side migration analysis (which pipeline features → Rust)

Sweet spot = the **classical-CV + vector-math layer**: pure, deterministic, model-free.
A Rust crate here runs **both** natively in the pipeline *and* as WASM in the client —
one source of truth (Python can't cross to the browser). Ranked candidates:

- **★★★** blur score (`quality.py`, Laplacian variance), `ClassicalToneEstimator`
  (`tone_estimator.py`, mean-luma / gray-world WB / clip fractions), LAION aesthetic
  **head** (`aesthetic.py`, small MLP on the CLIP embedding), select/dedup
  (`select.py`, MMR + CLIP cosine — the `core` crate already does the KNN half).
- **★★** `cluster.py` (temporal gaps + DBSCAN over GPS), enhance tone math.
- **Leave in JS/ONNX**: CLIP + captioning **models** (transformers.js/WebGPU or API).
- **Leave server-side / I/O**: ingest, sync (Immich), fulfill (Lulu).

### darktable / print rendering

`render.py` shells out to `darktable-cli` (RAW/original + XMP history of 4 bounded
knobs → corrected JPEG). Findings:
- **Reproducing darktable's exact output in-browser: impractical.** Its demosaic /
  highlight-recon / filmic / color science aren't ported to WASM and are version-
  sensitive (pipeline pins darktable 5.0.1).
- **Rendering RAW to print quality in-browser: feasible, as your *own* renderer** —
  `rawloader` + `imagepipe` (pure Rust → WASM) for decode/demosaic, WGSL for tone,
  `qcms`/lcms2 for ICC, full-res 16-bit output. Different look, not a darktable clone.
- **The 4 enhance knobs are trivially reproducible** client-side (exposure already is).
- **Recommended split**: browser = live *preview/proofing* (Rust/WGSL, GPU); server =
  canonical RAW→print render (darktable). Note GPU floats aren't cross-device
  deterministic — a print path would want a deterministic CPU Rust renderer.

### RAW→PDF: proven in-browser (rawpdf crate, `/rawpdf.html`)

Vertical slice built + verified live: a real iPhone DNG (4032×3024, lossy-JPEG DNG)
→ **`rawloader` decode in wasm32** → crude half-res demosaic (WB + gamma, correct
colour — a ColorChecker renders right) → **`printpdf` one-page PDF** (valid `%PDF-1.3`,
1.76 MB), **~840 ms** end to end. Both ecosystem risks retired: pure-Rust RAW decode
and PDF generation both compile *and run* in the browser. Crudeness noted (half-res,
no EXIF orientation → image is flipped, no ICC) — production would use imagepipe/rawler
+ moxcms. Verified crate list from the ecosystem sweep — rawloader/rawler (LGPL),
moxcms (permissive CMYK/ICC), fast_image_resize (WASM-proven), printpdf (WASM-proven).

### Correction: the RAW/print lane is NOT Rust-only (web scout, 2026-07)

An earlier framing claimed "no real JS equivalent" for the RAW→develop→print-PDF
pipeline, so Rust owned it by default. A web scout retired that claim — mature
JS/WASM options exist for every leg, some *ahead* of the Rust crates above:

- **RAW decode** — [LibRaw-Wasm](https://github.com/ybouane/LibRaw-Wasm): LibRaw
  (the standard RAW engine) compiled to WASM, Web Worker, CR2/NEF/ARW/DNG… +
  demosaic. **Broader format coverage than `rawloader`**, already shipped in prod
  ([pics.io](https://blog.pics.io/in-browser-raw-processing-how-we-did-it-6f03f1e9fe4c/)).
  Pure-JS [UTIF.js](https://github.com/photopea/UTIF.js/) covers the TIFF-ish subset.
- **ICC / CMYK color management** — [lcms-wasm](https://www.npmjs.com/package/lcms-wasm)
  (Little-CMS as WASM; FOGRA39/SWOP) and [jsColorEngine](https://github.com/glennwilton/jsColorEngine)
  (**100% pure JS**, ICC v2+v4, RGB/CMYK/Lab/XYZ).
- **Print-ready PDF** — [pdfkit](https://github.com/foliojs/pdfkit) +
  [pdfkit-cmyk](https://www.npmjs.com/package/pdfkit-cmyk), and [IMG.LY's Print-Ready
  PDF plugin](https://img.ly/docs/cesdk/js/plugins/print-ready-pdf-iroalu/) doing
  **PDF/X-4, CMYK, ICC embedding, client-side** — arguably ahead of `printpdf` on
  print-spec compliance.

Catch: LibRaw-Wasm and lcms-wasm are themselves WASM (C), just driven from JS — so
"Rust is faster / SIMD" is **not** a differentiator against them; they're equally
native-speed.

**Revised verdict.** With sqlite-vec owning storage+KNN (the Rust `core` VecStore's
original job — now redundant), the honest Rust surface shrinks to:
- **GPU viewer (wgpu): still Rust-only.** No JS equivalent to owning the `<canvas>`
  at the WebGPU/shader level. Keep.
- **RAW/print pipeline: Rust-*optional*, not Rust-only.** It competes with
  LibRaw-Wasm + jsColorEngine/lcms-wasm + a CMYK PDF lib, which is the **lower-effort
  path** for batch book export. Rust's only real edge here is *coherence with the
  viewer*: develop/tone/resize in Rust share image types and one wasm memory with the
  GPU viewer, so pixels never cross JS↔WASM between decode→develop→display→export
  (the JS stack copies buffers across three separate WASM modules). That matters for
  GPU-side develop + live proofing; it's ~irrelevant for plain batch export.

So: build Rust here only if you want the single-memory / GPU-develop coherence.
Otherwise the JS/WASM stack ships the print book faster.

### Data layer: SQLite in the browser (built + verified; Rust `core` VecStore retired)

The R2 fallback (hand-written cosine KNN in the Rust `core` crate) was replaced by a real
browser SQLite store. Two build-reality constraints surfaced while wiring it up and
reshaped the design from the original vec0-on-OPFS plan:

SQLite has no pluggable vector column/index like pgvector, so a `vec0` **virtual table**
is the correct hook (custom shadow-table storage + a `MATCH … ORDER BY distance` operator;
join to rows by `rowid`). Getting a *working* vec0 build in the browser took two tries,
and OPFS turned out to be unavailable on the main thread:

1. **The ad-hoc demo build is broken.** `sqlite-vec-wasm-demo@0.1.9` **aborts on `init()`**
   in current Chrome (Emscripten *"set Module.postRun after already processed"*) —
   reproduced across static import, dynamic import, **and its own jsDelivr CDN**; it's
   self-labelled "a demonstration and may change at any time." → rejected.
2. **`sqlite-wasm-vec` works.** A fork of the *official* `@sqlite.org/sqlite-wasm` (SQLite
   3.51) with **sqlite-vec `v0.1.7-alpha.2` statically compiled in**. Because it's built
   the official way, it inits cleanly where the demo doesn't. This is what ships.
3. **OPFS SAHPool is Worker-only here.** Probed directly: `createSyncAccessHandle` is
   **absent on the main thread, present in a Worker**. The first cut avoided the Worker and
   kept the DB in memory on the main thread, persisting via an IndexedDB snapshot. That was
   later **superseded** (below): the DB now runs in a Worker on OPFS, and the worry that "a
   Worker complicates the vec0 path" did not hold — nothing vec0-specific breaks in a Worker.

**Shipped design** (works; verified live in Chrome — real vec0):
- **Threading**: the whole DB lives in a **Web Worker** (`lib/db.worker.ts`), fronted on
  the main thread by **Comlink** (`lib/store.ts`) so callers use it like a normal async
  module (`await knn(vec, k)` is a postMessage round-trip). This (a) unlocks OPFS SAHPool
  (Worker-only) and (b) keeps the O(n) brute-force KNN scan + inserts off the render thread
  so they can't jank the wgpu viewer. The public store API is unchanged; the former **sync**
  getters (`knn`/`count`/`listPhotos`/`getEmbedding`/`deletePhoto`) are now **async**.
- **Engine**: **`sqlite-wasm-vec` 0.1.11** (official sqlite-wasm + vec0). Loaded *inside the
  worker* as a **raw static asset** from `public/sqlite/` (staged by
  `scripts/copy-sqlite.mjs`, `postinstall`) via a `new Function('u','return import(u)')`
  shim — Vite's module transform breaks the Emscripten init and rewrites dynamic imports
  (`?import`), so it must bypass the bundler entirely. The worker itself *is* bundled by
  Vite (`new Worker(new URL('./db.worker.ts', import.meta.url), {type:'module'})`).
- **KNN**: real `vec0` — `CREATE VIRTUAL TABLE vec_photos USING vec0(embedding
  float[dim] distance_metric=cosine)`, queried with `WHERE embedding MATCH ? ORDER BY
  distance LIMIT k`. Cosine matches the server's pgvector similarity. (An earlier interim
  built a `cosine_distance` scalar UDF for the official no-vec build; dropped once
  `sqlite-wasm-vec` proved out.)
- **Persistence**: **OPFS via `installOpfsSAHPoolVfs`** in the worker — SQLite writes pages
  straight to OPFS, so persistence is automatic (no snapshotting). This replaced the earlier
  IndexedDB-snapshot approach, whose `sqlite3_js_db_export` re-serialized the *whole* DB on
  every change (grew with the library); OPFS writes only dirty pages. The vec0 shadow tables
  are ordinary tables, so they persist transparently. Falls back to an in-memory DB if OPFS
  is unavailable. Verified live: insert 3 → reload → `existing:3`, and vec0 `MATCH` KNN is
  identical (order + cosine distances) against the reopened OPFS DB.
  - **Reload lock race + fix.** SAHPool takes **exclusive** sync-access handles; on reload a
    navigated-away worker can still hold them, so a fresh acquire transiently throws and
    would silently fall back to in-memory. Fixed with two mitigations: `store.ts` terminates
    the worker on `pagehide` (releases handles promptly) **and** `openOpfs` retries
    acquisition for ~4 s before falling back. Bounded so a *second live tab* (which holds the
    lock indefinitely — SAHPool is single-connection) eventually falls back rather than
    hanging. Verified: single reload reliably reacquires OPFS; rapid back-to-back loads can
    still momentarily fall back, then recover on the next load.
- **ORM**: **Drizzle** for the typed relational read/insert (`photos`); raw SQL for the
  vec0 insert + `MATCH` KNN (no ORM models a virtual table).
- **Backend-agnostic seam**: `knn()` / `getByHash()` / `putEmbedding()` — the engine can
  swap (UDF ↔ vec0 ↔ future ANN) without touching callers.

**Photos on disk (`lib/files.ts`, verified live).** Image *bytes* stay out of SQLite (they'd
bloat the OPFS DB file and every KNN table scan). Instead, per photo we persist in a separate
IndexedDB DB:
- a **`FileSystemFileHandle`** — a re-openable *reference* to the user's original file
  (File System Access API; **Chromium-only**; structured-cloneable so it survives reload).
  Captured from `showOpenFilePicker()` or drag-drop `getAsFileSystemHandle()`. Re-granting
  read permission needs a user gesture, so full-res resolves lazily on click
  (`resolveFullRes` → `none`/`denied`/`missing`/`ok`). We never expose a path and never
  write the user's file.
- a small **JPEG thumbnail** — the gallery repopulates instantly on reload from DB rows +
  thumbnails, with **no permission prompt**; full-res loads from disk only when opened.
- **Delete** drops the SQLite row + vector and the IndexedDB handle+thumbnail — the
  on-disk original is left untouched (we remove our *reference*, not the file).
Verified: thumbnail persist/restore, handle survives reload → resolves full-res, moved
file → `missing`, no-handle (input fallback) → `none`, delete persists across reload.

Consequence: Rust's clearly-justified surface narrows to the **wgpu viewer**; the data
leg is SQL/JS, and the RAW/print leg is Rust-optional (see correction above).

## Out of scope (this spike)
Face detection, aesthetic scoring, classifiers, captioning VLM, neural/RAW
enhancement, any connection to the Python pipeline / Postgres / Immich, ANN/HNSW
indexing, OPFS persistence tuning, production polish. These are the *next*
questions the findings will inform — captured as follow-ups here, not built now.

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
> [ML boundary & quality-signal exploration](/build/drafts/ml-and-quality-exploration.md).
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

**Remaining:** step 4 (Rust/wgpu viewer) — the ambitious R1 piece; Canvas2D backend
is in place behind the same `Viewer` interface, so the swap is localized. Not yet
committed (on branch `spike-rust-client`).

## Out of scope (this spike)
Face detection, aesthetic scoring, classifiers, captioning VLM, neural/RAW
enhancement, any connection to the Python pipeline / Postgres / Immich, ANN/HNSW
indexing, OPFS persistence tuning, production polish. These are the *next*
questions the findings will inform — captured as follow-ups here, not built now.

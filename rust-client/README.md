# rust-client

A greenfield spike testing how much of the photobook analysis/select stack can run
**client-side in the browser** — with a **JS shell hosting Rust/WASM "islands"**
(polyglot components). Design brief + findings:
[`knowledge/build/drafts/rust-client.md`](../knowledge/build/drafts/rust-client.md).

Deliberately **outside** the Python pipeline's gate — it's a separate stack, not
wired into Postgres/Immich.

## Architecture

```
Svelte + shadcn-svelte tokens (Vite + TS)     ← chrome, gallery, controls, state
  ├─ viewer (Rust → WASM, wgpu) ──► <canvas>   ← owns the canvas: texture + exposure shader (pixels)
  ├─ transformers.js (WebGPU) ──► CLIP          ← image → 512-d embedding (the "model" leg, JS)
  └─ core (Rust → WASM) ──► VecStore            ← store embedding + exact cosine KNN (the "data" leg)
```

- **`src/`** — Svelte shell. `lib/clip.ts` (CLIP via transformers.js),
  `lib/store.ts` (wraps the Rust store), `lib/viewer.ts` (photo viewer).
- **`core/`** — Rust crate → WASM: `VecStore` = exact brute-force cosine KNN over
  L2-normalized embeddings. The "data lives in Rust" leg.
- **`viewer/`** — Rust crate → WASM: a **wgpu** photo viewer that creates a GPU
  surface on the shell's `<canvas>`, uploads the image as an sRGB texture, and
  renders a contain-fit quad with a live exposure gain (WGSL fragment shader). One
  codebase, two backends: **WebGPU** preferred, **WebGL2** fallback (naga transpiles
  the WGSL to GLSL). The Canvas2D viewer in `lib/viewer.ts` is a last resort for
  browsers with neither.
- **`src/wasm/`** — generated wasm-pack output, **committed on purpose** (tiny;
  keeps `npm run check` / dev build-free). Regenerate with `npm run wasm`.

## Prerequisites

- Node + npm
- Rust toolchain with the wasm target: `rustup target add wasm32-unknown-unknown`
- `wasm-pack`: `curl -sSf https://rustwasm.github.io/wasm-pack/installer/init.sh | sh`
  (ensure `~/.cargo/bin` is on `PATH`)
- A **WebGPU-capable browser** (Chrome/Edge, or Safari 18+) for GPU-accelerated
  CLIP. Without WebGPU it falls back to the (much slower) WASM backend.

## Run

```sh
npm install
npm run wasm     # build the Rust/WASM crates into src/wasm/ (only needed if changed)
npm run dev      # http://localhost:5173
```

Then **drop image files** onto the gallery (or click **+ Add**). Each is embedded
with CLIP, stored in the Rust `VecStore`, and shown as a thumbnail. Click a photo
to view it; drag **Exposure** for a live adjustment; **Find similar** runs the Rust
KNN and outlines the nearest neighbours in green.

## Scripts

| Script | Does |
| --- | --- |
| `npm run dev` | Vite dev server |
| `npm run build` | Production bundle |
| `npm run check` | svelte-check + tsc (type gate) |
| `npm run wasm` | Build both Rust crates to `src/wasm/` (deletes the `*`-only `.gitignore` wasm-pack writes, which otherwise hides the generated `.d.ts` from svelte-check) |

## Status

✅ **All four steps done & verified in-browser** (Chrome, WebGPU): Svelte/shadcn
shell, CLIP embeddings on WebGPU (512-d), Rust/WASM exact-KNN "Find similar", and
the **Rust/wgpu viewer** (GPU texture render + live exposure shader). Both Rust
crates build with `npm run wasm`.

## Notes / decisions

- **R2 — sqlite-vec deferred.** Compiling C-SQLite + the extension to
  `wasm32-unknown-unknown` needs a WASI/emscripten libc; the spike uses an exact
  Rust cosine KNN instead (same non-ANN shape as sqlite-vec). `store.ts` is
  backend-agnostic, so sqlite-vec can slot in later.
- **Embedding dim** defaults to 512 (`clip-vit-base-patch32`); switch `CLIP_MODEL`
  in `lib/clip.ts` to a ViT-L/14 for 768-d parity with the server's pgvector column.
- **COOP/COEP headers** are left off (see `vite.config.ts`) so transformers.js can
  fetch weights from the HF CDN; that means no SharedArrayBuffer / wasm threads for now.
- **wgpu ≥ 23 required.** wgpu 22 requests the `maxInterStageShaderComponents` limit
  that current Chrome removed from the WebGPU spec, so `requestDevice` rejects it.
- **Graceful GPU degradation.** The viewer is one wgpu codebase over **WebGPU +
  WebGL2**, so it renders on essentially any modern browser; Canvas2D is only a last
  resort. CLIP still keys off a `lib/gpu.ts` `requestAdapter()` probe to pick its
  `webgpu` vs `wasm` backend (transformers.js has no WebGL path). The status bar shows
  the viewer's actual backend (`webgpu` / `webgl2` / `canvas2d`).
- **Theme-aware viewer background.** The wgpu clear colour is set from the page's card
  colour (read via `getComputedStyle`, rasterized through a 1×1 2D canvas to normalize
  oklch → sRGB, then converted to linear for the sRGB surface), so the letterbox
  matches the surrounding UI in both light and dark themes.

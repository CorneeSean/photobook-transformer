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
  ├─ <canvas> viewer ───► pixels               ← Canvas2D today; Rust/wgpu next (see status)
  ├─ transformers.js (WebGPU) ──► CLIP          ← image → 512-d embedding (the "model" leg, JS)
  └─ core (Rust → WASM) ──► VecStore            ← store embedding + exact cosine KNN (the "data" leg)
```

- **`src/`** — Svelte shell. `lib/clip.ts` (CLIP via transformers.js),
  `lib/store.ts` (wraps the Rust store), `lib/viewer.ts` (photo viewer).
- **`core/`** — Rust crate → WASM: `VecStore` = exact brute-force cosine KNN over
  L2-normalized embeddings. The "data lives in Rust" leg.
- **`viewer/`** — Rust crate → WASM for the wgpu photo viewer (placeholder until
  step 4 lands; the Canvas2D viewer in `lib/viewer.ts` drives the UI meanwhile).
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

- ✅ **Step 1–3 done & verified in-browser**: Svelte/shadcn shell, CLIP embeddings
  on WebGPU (512-d), Rust/WASM exact-KNN "Find similar", Canvas2D viewer with live
  exposure.
- ⏳ **Step 4 (Rust/wgpu viewer)** — pending; the Canvas2D viewer is the current
  backend behind the same `Viewer` interface, so swapping it in is localized.

## Notes / decisions

- **R2 — sqlite-vec deferred.** Compiling C-SQLite + the extension to
  `wasm32-unknown-unknown` needs a WASI/emscripten libc; the spike uses an exact
  Rust cosine KNN instead (same non-ANN shape as sqlite-vec). `store.ts` is
  backend-agnostic, so sqlite-vec can slot in later.
- **Embedding dim** defaults to 512 (`clip-vit-base-patch32`); switch `CLIP_MODEL`
  in `lib/clip.ts` to a ViT-L/14 for 768-d parity with the server's pgvector column.
- **COOP/COEP headers** are left off (see `vite.config.ts`) so transformers.js can
  fetch weights from the HF CDN; that means no SharedArrayBuffer / wasm threads for now.

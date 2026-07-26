import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte(), tailwindcss()],

  // The Rust/WASM viewer crate is built with `wasm-pack --target web` into
  // src/wasm/viewer; its generated JS glue + .wasm are imported directly.
  // transformers.js ships its own worker/wasm assets (from the HF CDN) and must not be
  // pre-bundled. (sqlite-wasm is loaded as a raw asset from public/sqlite/ inside the DB
  // worker — see lib/db.worker.ts — precisely because Vite's module transform breaks its
  // Emscripten init. The DB worker itself is bundled by Vite via `new Worker(new URL(...))`.)
  optimizeDeps: {
    exclude: ['@huggingface/transformers'],
  },

  // Two pages: the main photo app (index.html) and the RAW→PDF proof (rawpdf.html).
  build: {
    rollupOptions: {
      input: {
        main: 'index.html',
        rawpdf: 'rawpdf.html',
      },
    },
  },

  // NOTE (R4): SharedArrayBuffer / wasm threads need COOP+COEP headers here:
  //   server: { headers: { 'Cross-Origin-Opener-Policy': 'same-origin',
  //                         'Cross-Origin-Embedder-Policy': 'require-corp' } }
  // Left OFF for the spike: COEP `require-corp` blocks the transformers.js model
  // download from the HF CDN (no CORP header there). Single-threaded WASM + WebGPU
  // works without it. Revisit if we self-host weights or need threads.
})

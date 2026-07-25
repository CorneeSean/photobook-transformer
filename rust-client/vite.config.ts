import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte(), tailwindcss()],

  // The Rust/WASM crates are built with `wasm-pack --target web` into
  // src/wasm/{core,viewer}; their generated JS glue + .wasm are imported directly.
  // Exclude transformers.js from dep pre-bundling so its worker/wasm assets resolve.
  optimizeDeps: {
    exclude: ['@huggingface/transformers'],
  },

  // NOTE (R4): SharedArrayBuffer / wasm threads need COOP+COEP headers here:
  //   server: { headers: { 'Cross-Origin-Opener-Policy': 'same-origin',
  //                         'Cross-Origin-Embedder-Policy': 'require-corp' } }
  // Left OFF for the spike: COEP `require-corp` blocks the transformers.js model
  // download from the HF CDN (no CORP header there). Single-threaded WASM + WebGPU
  // works without it. Revisit if we self-host weights or need threads.
})

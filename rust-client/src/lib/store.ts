// The "data" leg of the spike: a vector store that lives in Rust/WASM. The shell
// hands it CLIP embeddings and asks for nearest neighbours; all the vector math
// runs in Rust, not JS.
//
// R2 note: the intended backend was sqlite-vec, but compiling C-SQLite +
// the extension to wasm32-unknown-unknown is a yak-shave (needs a WASI/emscripten
// libc). For the spike we took the documented fallback — an exact brute-force
// cosine KNN implemented in Rust (`core` crate). Same shape as sqlite-vec's own
// (non-ANN) KNN, so a sqlite-vec backend can slot in behind this same interface later.

import initCore, { VecStore } from '../wasm/core/rust_client_core'

let ready: Promise<void> | null = null
let store: VecStore | null = null

/** Initialize the WASM module + an empty store of the given embedding dimension. */
export async function initStore(dim: number): Promise<void> {
  if (!ready) {
    ready = initCore().then(() => {
      store = new VecStore(dim)
    })
  }
  await ready
}

function get(): VecStore {
  if (!store) throw new Error('store not initialized — call initStore(dim) first')
  return store
}

/** Store one embedding under an integer id (the photo's index in the gallery). */
export function insert(id: number, embedding: Float32Array): void {
  get().insert(id, embedding)
}

/** k nearest ids to `query` by cosine similarity, nearest first (may include self). */
export function knn(query: Float32Array, k: number): Uint32Array {
  return get().knn(query, k)
}

/** Number of vectors currently stored. */
export function count(): number {
  return get().len()
}

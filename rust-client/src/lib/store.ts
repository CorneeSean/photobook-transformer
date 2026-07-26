// The "data" leg — thin main-thread client over the SQLite/sqlite-vec Worker.
//
// All the real work (sqlite-wasm-vec, the vec0 virtual table, OPFS SAHPool persistence)
// lives in db.worker.ts. This module wraps that Worker with Comlink so callers use it like
// a normal async module — `await knn(vec, k)` is a postMessage round-trip under the hood.
//
// Everything here is async by construction (the Worker boundary): what used to be the sync
// getters (knn/count/listPhotos/getEmbedding/deletePhoto) now return Promises.
//
// Vectors (Float32Array) cross the boundary by structured clone. They're tiny (a 768-d
// embedding is ~3 KB), so we don't bother with Comlink.transfer — cloning is cheap and,
// unlike transfer, doesn't detach a buffer the caller may still hold (e.g. a cached
// `selected.embedding` reused across a later knn call).
import * as Comlink from 'comlink'
import type { DbApi } from './db.worker'

// Vite bundles this Worker (module type) and rewrites the URL; the sqlite raw-asset import
// inside it still resolves at runtime (see db.worker.ts header).
const worker = new Worker(new URL('./db.worker.ts', import.meta.url), { type: 'module' })
const api = Comlink.wrap<DbApi>(worker)

// OPFS SAHPool holds *exclusive* sync-access handles; a navigated-away worker can keep them
// locked until it's GC'd, making the next page transiently fall back to in-memory. Terminate
// eagerly on pagehide so the handles release promptly and the next load reacquires OPFS.
addEventListener('pagehide', () => worker.terminate())

/** Open (or create) the persisted store for `dim`-dimensional embeddings. */
export function openStore(dim: number): Promise<{ persistent: boolean; existing: number }> {
  return api.open(dim)
}

/** Look up a photo by content hash; returns its id + stored embedding, or null. */
export function getByHash(hash: string): Promise<{ id: number; embedding: Float32Array } | null> {
  return api.getByHash(hash)
}

/** Insert a new photo row + its embedding, returning the shared rowid. */
export function putEmbedding(hash: string, name: string, embedding: Float32Array): Promise<number> {
  return api.putEmbedding(hash, name, embedding)
}

/** k nearest photo ids to `query` by cosine distance, nearest first (may include self). */
export function knn(query: Float32Array, k: number): Promise<{ id: number; distance: number }[]> {
  return api.knn(query, k)
}

/** Remove a photo's row + its vector. Does not touch any on-disk file (see files.ts). */
export function deletePhoto(id: number): Promise<void> {
  return api.deletePhoto(id)
}

/** Number of photos stored. */
export function count(): Promise<number> {
  return api.count()
}

/** All stored photos (metadata only), oldest first — to restore the gallery on boot. */
export function listPhotos(): Promise<{ id: number; hash: string; name: string }[]> {
  return api.listPhotos()
}

/** The stored embedding for a photo id (lazy-loaded for a restored photo on demand). */
export function getEmbedding(id: number): Promise<Float32Array | null> {
  return api.getEmbedding(id)
}

/** SHA-256 of a blob's bytes, hex — the stable per-photo key used for cache hits. Runs on
 *  the main thread: it's a pure Web Crypto call that never touches the DB, so shipping the
 *  Blob to the Worker just to hash it would waste a copy. */
export async function hashBlob(b: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await b.arrayBuffer())
  return Array.from(new Uint8Array(digest), (x) => x.toString(16).padStart(2, '0')).join('')
}

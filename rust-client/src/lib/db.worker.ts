// The "data" leg, in a Worker — persistent SQLite + sqlite-vec off the main thread.
//
// Why a Worker: sqlite-vec's `vec0` virtual table ships via the `sqlite-wasm-vec` build
// (official @sqlite.org/sqlite-wasm with vec0 compiled in). Real persistence wants OPFS,
// and OPFS SAHPool's sync-access handles are Worker-only in this Chrome. Running the DB
// here unlocks OPFS *and* keeps the O(n) brute-force KNN scan off the render thread.
//
// Two things this buys over the old main-thread + IndexedDB-snapshot approach:
//   - incremental page writes (SQLite → OPFS) instead of re-serializing the whole DB on
//     every change (`sqlite3_js_db_export`), which grew with the library.
//   - the KNN scan and inserts can't jank the UI / wgpu viewer loop.
//
// The sqlite-wasm Emscripten module must be loaded *untransformed*, so it's served as a raw
// asset from public/sqlite/ and imported by URL via a `new Function` shim (Vite's transform
// breaks the Emscripten init + rewrites the import). Same trick works inside the Worker.
//
// Layering: Drizzle owns the typed relational read (getByHash); the vec0 insert/KNN are raw
// SQL — no ORM models a virtual table or its MATCH operator. This module is exposed to the
// main thread via Comlink (see store.ts); every method reads as a normal async call there.
import * as Comlink from 'comlink'
import { drizzle, type SqliteRemoteDatabase } from 'drizzle-orm/sqlite-proxy'
import { eq } from 'drizzle-orm'
import { photos } from './schema'
import type { OoDb, Sqlite3, Sqlite3InitModule } from './sqlite-types'

const DB_FILE = '/photobook.sqlite'
const VFS_NAME = 'photobook-opfs'

// Load an ESM by URL at runtime, bypassing the bundler's import transform (Vite otherwise
// rewrites dynamic imports and appends `?import`, breaking the raw public asset fetch).
// Hiding `import()` inside `new Function` keeps Vite from seeing it — even when bundling
// this worker.
const dynamicImport = new Function('u', 'return import(u)') as (u: string) => Promise<unknown>

let sqlite3: Sqlite3 | null = null
let raw: OoDb | null = null
let db: SqliteRemoteDatabase<Record<string, never>> | null = null
let dims = 0

function rawDb(): OoDb {
  if (!raw) throw new Error('store not opened — call open(dim) first')
  return raw
}
function orm(): SqliteRemoteDatabase<Record<string, never>> {
  if (!db) throw new Error('store not opened — call open(dim) first')
  return db
}

/** A Float32 vector as a raw little-endian BLOB (how sqlite-vec stores float[] vectors). */
function toBlob(v: Float32Array): Uint8Array {
  return new Uint8Array(v.buffer, v.byteOffset, v.byteLength)
}
/** A BLOB back to a Float32 vector (copied to an aligned, offset-0 buffer). */
function toVec(blob: Uint8Array): Float32Array {
  const copy = blob.slice()
  return new Float32Array(copy.buffer, 0, copy.byteLength / 4)
}

function readEmbedding(id: number): Float32Array | null {
  const blob = rawDb().selectValue('SELECT embedding FROM vec_photos WHERE rowid = ?', [id]) as
    | Uint8Array
    | undefined
  return blob ? toVec(blob) : null
}

function migrate(d: OoDb, dim: number): void {
  d.exec(`CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)`)
  const stored = d.selectValue(`SELECT value FROM meta WHERE key = 'dim'`) as string | undefined
  if (stored !== undefined && Number(stored) !== dim) {
    d.exec(`DROP TABLE IF EXISTS vec_photos`)
    d.exec(`DROP TABLE IF EXISTS photos`)
  }
  d.exec(`
    CREATE TABLE IF NOT EXISTS photos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      hash TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL,
      added_at INTEGER NOT NULL
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS vec_photos
      USING vec0(embedding float[${dim}] distance_metric=cosine);
  `)
  d.exec({ sql: `INSERT OR REPLACE INTO meta(key, value) VALUES ('dim', ?)`, bind: [String(dim)] })
}

/**
 * Open the OPFS SAHPool DB, retrying briefly on failure. SAHPool takes *exclusive*
 * sync-access handles on its OPFS files; on a reload the previous tab's worker may still be
 * releasing them (its teardown outlives the navigation), so a fresh acquire can transiently
 * throw. Retrying for a few seconds turns that race into a short wait instead of a silent
 * fall-back to a non-persistent in-memory DB. The window is bounded so a *second live tab*
 * (which holds the lock indefinitely) eventually falls back rather than hanging forever.
 */
async function openOpfs(s: Sqlite3): Promise<OoDb> {
  let lastErr: unknown
  for (let attempt = 0; attempt < 20; attempt++) {
    try {
      const pool = await s.installOpfsSAHPoolVfs({ name: VFS_NAME })
      return new pool.OpfsSAHPoolDb(DB_FILE)
    } catch (err) {
      lastErr = err
      await new Promise((r) => setTimeout(r, 200))
    }
  }
  throw lastErr
}

/** Drizzle "proxy" driver: run SQL against the synchronous oo1 DB, shaped for drizzle-orm. */
function driver(d: OoDb) {
  return async (sql: string, params: unknown[], method: 'run' | 'all' | 'values' | 'get') => {
    if (method === 'run') {
      d.exec({ sql, bind: params })
      return { rows: [] as unknown[] }
    }
    const rows = d.selectArrays(sql, params)
    return { rows: method === 'get' ? (rows[0] ?? []) : rows }
  }
}

const api = {
  /**
   * Open (or create) the OPFS-backed store for `dim`-dimensional embeddings. Persistence is
   * automatic (SQLite writes pages straight to OPFS). Falls back to an in-memory DB if OPFS
   * SAHPool is unavailable. Returns whether persistence is active + how many vectors exist.
   */
  async open(dim: number): Promise<{ persistent: boolean; existing: number }> {
    dims = dim
    // Raw static asset (see header + dynamicImport note).
    const mod = (await dynamicImport('/sqlite/sqlite3.mjs')) as { default: Sqlite3InitModule }
    sqlite3 = await mod.default()

    let persistent = false
    try {
      raw = await openOpfs(sqlite3)
      persistent = true
    } catch (err) {
      console.warn('[db.worker] OPFS SAHPool unavailable — running in-memory (no persistence)', err)
      raw = new sqlite3.oo1.DB(':memory:')
      persistent = false
    }

    migrate(raw, dim)
    db = drizzle(driver(raw))
    const existing = raw.selectValue('SELECT count(*) FROM photos') as number
    return { persistent, existing }
  },

  /** Look up a photo by content hash; returns its id + stored embedding, or null. */
  async getByHash(hash: string): Promise<{ id: number; embedding: Float32Array } | null> {
    const rows = await orm().select({ id: photos.id }).from(photos).where(eq(photos.hash, hash)).limit(1)
    if (!rows.length) return null
    const emb = readEmbedding(rows[0].id)
    return emb ? { id: rows[0].id, embedding: emb } : null
  },

  /** Insert a new photo row (Drizzle) + its embedding (vec0), returning the shared rowid. */
  async putEmbedding(hash: string, name: string, embedding: Float32Array): Promise<number> {
    if (embedding.length !== dims)
      throw new Error(`embedding dim ${embedding.length} ≠ store dim ${dims}`)
    const inserted = await orm()
      .insert(photos)
      .values({ hash, name, addedAt: Date.now() })
      .returning({ id: photos.id })
    const id = inserted[0].id
    rawDb().exec({ sql: 'INSERT INTO vec_photos(rowid, embedding) VALUES (?, ?)', bind: [id, toBlob(embedding)] })
    return id
  },

  /** k nearest photo ids to `query` by cosine distance, nearest first (vec0 KNN; may include self). */
  knn(query: Float32Array, k: number): { id: number; distance: number }[] {
    // k is a small integer we control, so inlining it (rather than binding) sidesteps any
    // vec0 parameter-in-LIMIT quirk; the only bound value is the query blob.
    const limit = Math.max(1, k | 0)
    return rawDb().selectObjects(
      `SELECT rowid AS id, distance FROM vec_photos WHERE embedding MATCH ? ORDER BY distance LIMIT ${limit}`,
      [toBlob(query)],
    ) as { id: number; distance: number }[]
  },

  /** Remove a photo's row + its vector. Does not touch any on-disk file (see files.ts). */
  deletePhoto(id: number): void {
    rawDb().exec({ sql: 'DELETE FROM vec_photos WHERE rowid = ?', bind: [id] })
    rawDb().exec({ sql: 'DELETE FROM photos WHERE id = ?', bind: [id] })
  },

  /** Number of photos stored. */
  count(): number {
    return rawDb().selectValue('SELECT count(*) FROM photos') as number
  },

  /** All stored photos (metadata only), oldest first — to restore the gallery on boot. */
  listPhotos(): { id: number; hash: string; name: string }[] {
    return rawDb().selectObjects('SELECT id, hash, name FROM photos ORDER BY id') as {
      id: number
      hash: string
      name: string
    }[]
  },

  /** The stored embedding for a photo id (lazy-loaded for a restored photo on demand). */
  getEmbedding(id: number): Float32Array | null {
    return readEmbedding(id)
  },
}

export type DbApi = typeof api

Comlink.expose(api)

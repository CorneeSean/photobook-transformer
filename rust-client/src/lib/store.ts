// The "data" leg — persistent SQLite + sqlite-vec in the browser.
//
// Backend note (2026-07): the plan was sqlite-vec's `vec0` virtual table, and that's what
// ships here — via the `sqlite-wasm-vec` build (official @sqlite.org/sqlite-wasm with the
// vec0 extension statically compiled in). The one *ad-hoc* demo build,
// `sqlite-vec-wasm-demo`, aborts on init in current Chrome (Emscripten postRun bug); the
// official-based `sqlite-wasm-vec` build inits cleanly. Vectors live in a `vec0` virtual
// table (SQLite has no pluggable vector column/index like pgvector — a virtual table is
// the only hook; join to relational rows by `rowid`). KNN is `MATCH … ORDER BY distance`
// with `distance_metric=cosine`, matching the server's pgvector cosine similarity.
//
// The sqlite-wasm Emscripten module must be loaded *untransformed*, so it's served as a raw
// asset from public/sqlite/ (staged by scripts/copy-sqlite.mjs) and imported by URL via a
// `new Function` shim (Vite's transform breaks the Emscripten init + rewrites the import).
//
// Persistence: OPFS SAHPool sync-access-handles are Worker-only in this Chrome, and a
// Worker complicates the vec0 path, so the DB is kept in memory on the main thread and
// snapshotted whole into IndexedDB (sqlite3_js_db_export → sqlite3_deserialize on boot).
// The vec0 shadow tables serialize with the DB, so persistence is transparent. Net effect
// matches OPFS: embeddings computed once ever, survive reload.
//
// Layering: Drizzle owns the typed relational read (getByHash); the vec0 insert/KNN are
// raw SQL — no ORM models a virtual table or its MATCH operator.
import { drizzle, type SqliteRemoteDatabase } from 'drizzle-orm/sqlite-proxy'
import { eq } from 'drizzle-orm'
import { photos } from './schema'
import type { OoDb, Sqlite3, Sqlite3InitModule } from './sqlite-types'

const IDB_DB = 'photobook-store'
const IDB_STORE = 'kv'
const SNAPSHOT_KEY = 'photobook.sqlite'

// Load an ESM by URL at runtime, fully bypassing the bundler's import transform (Vite
// otherwise rewrites dynamic imports and appends `?import`, which breaks fetching the raw
// public asset). Hiding `import()` inside `new Function` keeps Vite from seeing it.
const dynamicImport = new Function('u', 'return import(u)') as (u: string) => Promise<unknown>

let sqlite3: Sqlite3 | null = null
let raw: OoDb | null = null
let db: SqliteRemoteDatabase<Record<string, never>> | null = null
let dims = 0
let persistEnabled = false

function rawDb(): OoDb {
  if (!raw) throw new Error('store not opened — call openStore(dim) first')
  return raw
}
function orm(): SqliteRemoteDatabase<Record<string, never>> {
  if (!db) throw new Error('store not opened — call openStore(dim) first')
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

// ---- IndexedDB snapshot persistence ---------------------------------------

function idb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_DB, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(IDB_STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}
async function idbGet(key: string): Promise<Uint8Array | undefined> {
  const d = await idb()
  return new Promise((resolve, reject) => {
    const r = d.transaction(IDB_STORE, 'readonly').objectStore(IDB_STORE).get(key)
    r.onsuccess = () => resolve(r.result as Uint8Array | undefined)
    r.onerror = () => reject(r.error)
  })
}
async function idbPut(key: string, value: Uint8Array): Promise<void> {
  const d = await idb()
  return new Promise((resolve, reject) => {
    const tx = d.transaction(IDB_STORE, 'readwrite')
    tx.objectStore(IDB_STORE).put(value, key)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

let saveTimer: ReturnType<typeof setTimeout> | null = null
/** Snapshot the whole DB into IndexedDB (debounced). */
function scheduleSave(): void {
  if (!persistEnabled) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => void saveNow(), 300)
}
async function saveNow(): Promise<void> {
  if (!persistEnabled || !sqlite3 || !raw) return
  try {
    await idbPut(SNAPSHOT_KEY, sqlite3.capi.sqlite3_js_db_export(raw))
  } catch (err) {
    console.warn('[store] snapshot save failed', err)
  }
}

// ---- schema ----------------------------------------------------------------

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

/**
 * Open (or create) the persisted store for `dim`-dimensional embeddings.
 * Returns whether persistence is active and how many vectors were already on disk.
 */
export async function openStore(dim: number): Promise<{ persistent: boolean; existing: number }> {
  dims = dim
  // Raw static asset (see header + dynamicImport note).
  const mod = (await dynamicImport('/sqlite/sqlite3.mjs')) as { default: Sqlite3InitModule }
  sqlite3 = await mod.default()
  raw = new sqlite3.oo1.DB(':memory:')

  // Load the previous snapshot from IndexedDB, if any.
  try {
    const snap = await idbGet(SNAPSHOT_KEY)
    if (snap && snap.length) {
      const ptr = sqlite3.wasm.allocFromTypedArray(snap)
      sqlite3.capi.sqlite3_deserialize(
        raw,
        'main',
        ptr,
        snap.length,
        snap.length,
        sqlite3.capi.SQLITE_DESERIALIZE_FREEONCLOSE | sqlite3.capi.SQLITE_DESERIALIZE_RESIZEABLE,
      )
    }
    persistEnabled = true
  } catch (err) {
    console.warn('[store] IndexedDB unavailable — running in-memory (no persistence)', err)
    persistEnabled = false
  }

  migrate(raw, dim)
  db = drizzle(driver(raw))
  if (persistEnabled) {
    void navigator.storage?.persist?.()
    addEventListener('pagehide', () => void saveNow())
    scheduleSave() // persist the (possibly migrated) schema even before the first insert
  }
  const existing = raw.selectValue('SELECT count(*) FROM photos') as number
  return { persistent: persistEnabled, existing }
}

/** Look up a photo by content hash; returns its id + stored embedding, or null. */
export async function getByHash(
  hash: string,
): Promise<{ id: number; embedding: Float32Array } | null> {
  const rows = await orm().select({ id: photos.id }).from(photos).where(eq(photos.hash, hash)).limit(1)
  if (!rows.length) return null
  const emb = readEmbedding(rows[0].id)
  return emb ? { id: rows[0].id, embedding: emb } : null
}

/** Insert a new photo row (Drizzle) + its embedding (vec0), returning the shared rowid. */
export async function putEmbedding(
  hash: string,
  name: string,
  embedding: Float32Array,
): Promise<number> {
  if (embedding.length !== dims) throw new Error(`embedding dim ${embedding.length} ≠ store dim ${dims}`)
  const inserted = await orm()
    .insert(photos)
    .values({ hash, name, addedAt: Date.now() })
    .returning({ id: photos.id })
  const id = inserted[0].id
  rawDb().exec({ sql: 'INSERT INTO vec_photos(rowid, embedding) VALUES (?, ?)', bind: [id, toBlob(embedding)] })
  scheduleSave()
  return id
}

/** k nearest photo ids to `query` by cosine distance, nearest first (vec0 KNN; may include self). */
export function knn(query: Float32Array, k: number): { id: number; distance: number }[] {
  // k is a small integer we control, so inlining it (rather than binding) sidesteps any
  // vec0 parameter-in-LIMIT quirk; the only bound value is the query blob.
  const limit = Math.max(1, k | 0)
  return rawDb().selectObjects(
    `SELECT rowid AS id, distance FROM vec_photos WHERE embedding MATCH ? ORDER BY distance LIMIT ${limit}`,
    [toBlob(query)],
  ) as { id: number; distance: number }[]
}

/** Remove a photo's row + its vector. Does not touch any on-disk file (see files.ts). */
export function deletePhoto(id: number): void {
  rawDb().exec({ sql: 'DELETE FROM vec_photos WHERE rowid = ?', bind: [id] })
  rawDb().exec({ sql: 'DELETE FROM photos WHERE id = ?', bind: [id] })
  scheduleSave()
}

/** Number of photos stored. */
export function count(): number {
  return rawDb().selectValue('SELECT count(*) FROM photos') as number
}

/** All stored photos (metadata only), oldest first — to restore the gallery on boot. */
export function listPhotos(): { id: number; hash: string; name: string }[] {
  return rawDb().selectObjects('SELECT id, hash, name FROM photos ORDER BY id') as {
    id: number
    hash: string
    name: string
  }[]
}

/** The stored embedding for a photo id (lazy-loaded for a restored photo on demand). */
export function getEmbedding(id: number): Float32Array | null {
  return readEmbedding(id)
}

/** SHA-256 of a blob's bytes, hex — the stable per-photo key used for cache hits. */
export async function hashBlob(b: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await b.arrayBuffer())
  return Array.from(new Uint8Array(digest), (x) => x.toString(16).padStart(2, '0')).join('')
}

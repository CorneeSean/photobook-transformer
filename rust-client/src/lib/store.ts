// The "data" leg — persistent SQLite in the browser.
//
// Backend note (2026-07), two constraints discovered while wiring this up:
//   1. The only ready-made sqlite-vec (`vec0`) browser build (`sqlite-vec-wasm-demo`)
//      aborts on init in current Chrome (an Emscripten postRun bug, reproduced from its
//      own CDN too). So we ship on the official @sqlite.org/sqlite-wasm build and do KNN
//      with a `cosine_distance` scalar UDF — an exact brute-force scan, identical in
//      result to sqlite-vec's (non-ANN) KNN. A working vec0 build drops in behind this
//      same `knn()` seam later (swap the UDF scan for a MATCH query).
//   2. OPFS SAHPool (sync access handles) is Worker-only in this Chrome, and a Worker
//      can't host the JS UDF callback. So instead of an OPFS-backed file we keep the DB
//      in memory on the main thread and persist it by snapshotting the whole database
//      (sqlite3_js_db_export) into IndexedDB, reloading it on boot (sqlite3_deserialize).
//      The DB is small (a few KB/photo), so a debounced full snapshot is cheap. Net
//      effect is the same as OPFS for our purpose: embeddings computed once, survive reload.
//
// The sqlite-wasm Emscripten module must be loaded *untransformed*, so it's served as a
// raw asset from public/sqlite/ (staged by scripts/copy-sqlite.mjs) and imported by URL.
//
// Layering: Drizzle owns the typed relational read (getByHash); inserts and the KNN scan
// are raw SQL because they carry the embedding BLOB / the UDF expression.
import { drizzle, type SqliteRemoteDatabase } from 'drizzle-orm/sqlite-proxy'
import { eq } from 'drizzle-orm'
import { photos } from './schema'
import type { OoDb, Sqlite3, Sqlite3InitModule } from './sqlite-types'

const IDB_DB = 'photobook-store'
const IDB_STORE = 'kv'
const SNAPSHOT_KEY = 'photobook.sqlite'

// Load an ESM by URL at runtime, fully bypassing the bundler's import transform (Vite
// otherwise rewrites dynamic imports and appends `?import`, which breaks fetching the
// raw public asset). Hiding `import()` inside `new Function` keeps Vite from seeing it.
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

/** A Float32 vector as a raw little-endian BLOB. */
function toBlob(v: Float32Array): Uint8Array {
  return new Uint8Array(v.buffer, v.byteOffset, v.byteLength)
}
/** A BLOB back to a Float32 vector (copied to an aligned, offset-0 buffer). */
function toVec(blob: Uint8Array): Float32Array {
  const copy = blob.slice()
  return new Float32Array(copy.buffer, 0, copy.byteLength / 4)
}

function readEmbedding(id: number): Float32Array | null {
  const blob = rawDb().selectValue('SELECT embedding FROM photos WHERE id = ?', [id]) as
    | Uint8Array
    | undefined
  return blob ? toVec(blob) : null
}

/** cosine *distance* (1 - cosine similarity); smaller = more similar. */
function cosineDistance(a: Uint8Array, b: Uint8Array): number {
  const x = toVec(a)
  const y = toVec(b)
  let dot = 0
  let nx = 0
  let ny = 0
  for (let i = 0; i < x.length; i++) {
    dot += x[i] * y[i]
    nx += x[i] * x[i]
    ny += y[i] * y[i]
  }
  const denom = Math.sqrt(nx * ny)
  return denom ? 1 - dot / denom : 1
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

// ---- schema + KNN UDF ------------------------------------------------------

function migrate(d: OoDb, dim: number): void {
  d.exec(`CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)`)
  const stored = d.selectValue(`SELECT value FROM meta WHERE key = 'dim'`) as string | undefined
  if (stored !== undefined && Number(stored) !== dim) d.exec(`DROP TABLE IF EXISTS photos`)
  d.exec(`
    CREATE TABLE IF NOT EXISTS photos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      hash TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL,
      added_at INTEGER NOT NULL,
      embedding BLOB NOT NULL
    );
  `)
  d.exec({ sql: `INSERT OR REPLACE INTO meta(key, value) VALUES ('dim', ?)`, bind: [String(dim)] })
}

function registerUdf(d: OoDb): void {
  // UDFs live on the connection, so (re)register on every open.
  d.createFunction('cosine_distance', (_ctx, a, b) => cosineDistance(a as Uint8Array, b as Uint8Array), {
    arity: 2,
    deterministic: true,
  })
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
  const mod = (await dynamicImport('/sqlite/index.mjs')) as { default: Sqlite3InitModule }
  sqlite3 = await mod.default()
  raw = new sqlite3.oo1.DB(':memory:')
  registerUdf(raw)

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
    // Best-effort flush when the tab goes away, in case a debounced save is pending.
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

/** Insert a new photo + its embedding, returning the assigned id. */
export async function putEmbedding(
  hash: string,
  name: string,
  embedding: Float32Array,
): Promise<number> {
  if (embedding.length !== dims) throw new Error(`embedding dim ${embedding.length} ≠ store dim ${dims}`)
  // Raw insert: it carries the BLOB (kept off the ORM path to avoid browser Buffer quirks).
  rawDb().exec({
    sql: 'INSERT INTO photos(hash, name, added_at, embedding) VALUES (?, ?, ?, ?)',
    bind: [hash, name, Date.now(), toBlob(embedding)],
  })
  scheduleSave()
  return rawDb().selectValue('SELECT last_insert_rowid()') as number
}

/** k nearest photo ids to `query` by cosine distance, nearest first (exact scan; may include self). */
export function knn(query: Float32Array, k: number): { id: number; distance: number }[] {
  const limit = Math.max(1, k | 0)
  return rawDb().selectObjects(
    `SELECT id, cosine_distance(embedding, ?) AS distance FROM photos ORDER BY distance LIMIT ${limit}`,
    [toBlob(query)],
  ) as { id: number; distance: number }[]
}

/** Number of photos stored. */
export function count(): number {
  return rawDb().selectValue('SELECT count(*) FROM photos') as number
}

/** SHA-256 of a blob's bytes, hex — the stable per-photo key used for cache hits. */
export async function hashBlob(b: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await b.arrayBuffer())
  return Array.from(new Uint8Array(digest), (x) => x.toString(16).padStart(2, '0')).join('')
}

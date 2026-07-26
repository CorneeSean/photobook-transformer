// Minimal types for the sqlite-wasm "oo1" API (the sqlite-wasm-vec build — official
// @sqlite.org/sqlite-wasm with the sqlite-vec `vec0` extension compiled in). We load the
// module as a raw static asset (import('/sqlite/sqlite3.mjs')), so there's no importable
// package specifier to get types from — we cast the dynamic import to these shapes instead.

export interface OoDb {
  exec(sql: string): OoDb
  exec(opts: {
    sql: string
    bind?: unknown[]
    rowMode?: string | number
    resultRows?: unknown[]
    returnValue?: string
  }): OoDb
  selectValue(sql: string, bind?: unknown[]): unknown
  selectArrays(sql: string, bind?: unknown[]): unknown[][]
  selectObjects(sql: string, bind?: unknown[]): Record<string, unknown>[]
  close(): void
}

/** Returned by installOpfsSAHPoolVfs — its DB persists to OPFS (no COOP/COEP needed). */
export interface SAHPoolUtil {
  OpfsSAHPoolDb: new (filename: string) => OoDb
}

export interface Sqlite3 {
  oo1: { DB: new (filename?: string) => OoDb }
  installOpfsSAHPoolVfs(opts?: {
    name?: string
    initialCapacity?: number
    clearOnInit?: boolean
  }): Promise<SAHPoolUtil>
  // Whole-DB snapshot round-trip (used for IndexedDB persistence — see store.ts).
  capi: {
    sqlite3_js_db_export(db: OoDb): Uint8Array
    sqlite3_deserialize(
      db: OoDb,
      schema: string,
      data: number,
      szDb: number,
      szBuf: number,
      flags: number,
    ): number
    SQLITE_DESERIALIZE_FREEONCLOSE: number
    SQLITE_DESERIALIZE_RESIZEABLE: number
  }
  wasm: { allocFromTypedArray(a: Uint8Array): number }
}

export type Sqlite3InitModule = (config?: Record<string, unknown>) => Promise<Sqlite3>

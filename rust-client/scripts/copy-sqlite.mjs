// Stage the sqlite-wasm-vec build (official @sqlite.org/sqlite-wasm + the sqlite-vec
// `vec0` extension statically compiled in) into public/sqlite/ so it is served as a raw
// static asset (Vite does not transform public/). The sqlite-wasm Emscripten module must
// be loaded untransformed — importing it through the bundler's module pipeline breaks its
// runtime init. store.ts loads it via `import('/sqlite/sqlite3.mjs')`.
//
// We copy the "bundler-friendly" module (it locates sqlite3.wasm via import.meta.url, so
// the two files must sit side by side) as sqlite3.mjs + sqlite3.wasm.
import { mkdirSync, copyFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = join(here, '..', 'node_modules', 'sqlite-wasm-vec', 'sqlite-wasm', 'jswasm')
const dest = join(here, '..', 'public', 'sqlite')

mkdirSync(dest, { recursive: true })
copyFileSync(join(src, 'sqlite3-bundler-friendly.mjs'), join(dest, 'sqlite3.mjs'))
copyFileSync(join(src, 'sqlite3.wasm'), join(dest, 'sqlite3.wasm'))
console.log('[copy-sqlite] staged sqlite-wasm-vec sqlite3.mjs + sqlite3.wasm → public/sqlite/')

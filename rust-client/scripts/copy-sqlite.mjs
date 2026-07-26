// Stage the official @sqlite.org/sqlite-wasm build into public/sqlite/ so it is served
// as a raw static asset (Vite does not transform public/). The sqlite-wasm Emscripten
// module must be loaded untransformed — importing it through the bundler's module
// pipeline breaks its runtime init. store.ts loads it via `import('/sqlite/index.mjs')`.
import { mkdirSync, copyFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = join(here, '..', 'node_modules', '@sqlite.org', 'sqlite-wasm', 'dist')
const dest = join(here, '..', 'public', 'sqlite')

mkdirSync(dest, { recursive: true })
for (const f of ['index.mjs', 'sqlite3.wasm']) {
  copyFileSync(join(src, f), join(dest, f))
}
console.log(`[copy-sqlite] staged index.mjs + sqlite3.wasm → public/sqlite/`)

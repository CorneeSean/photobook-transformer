// Drizzle schema for the store (the ORM's job: typed relational rows). One table holds
// both a photo's metadata and its embedding BLOB; KNN is a raw-SQL scan over `embedding`
// using a `cosine_distance` UDF (see store.ts) — no ORM models that expression.
//
// `id` is the SQLite rowid alias. `hash` (content SHA-256) is the stable cross-session
// key, so re-adding the same file is a cache hit and skips the CLIP forward pass.
import { sqliteTable, integer, text, blob } from 'drizzle-orm/sqlite-core'

export const photos = sqliteTable('photos', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  hash: text('hash').notNull().unique(),
  name: text('name').notNull(),
  addedAt: integer('added_at').notNull(),
  /** CLIP embedding as a little-endian float32 BLOB. */
  embedding: blob('embedding', { mode: 'buffer' }).notNull(),
})

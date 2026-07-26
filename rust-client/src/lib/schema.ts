// Drizzle schema for the *relational* side of the store (the ORM's job). The embeddings
// live in a `vec0` virtual table (see store.ts) that no ORM can model, so KNN stays raw
// SQL — Drizzle owns only these plain rows.
//
// `id` is the SQLite rowid alias; the matching row in the `vec_photos` vec0 table is keyed
// by the same rowid, so a KNN hit joins straight back to a photo. `hash` (content SHA-256)
// is the stable cross-session key, so re-adding the same file is a cache hit that skips
// the CLIP forward pass.
import { sqliteTable, integer, text } from 'drizzle-orm/sqlite-core'

export const photos = sqliteTable('photos', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  hash: text('hash').notNull().unique(),
  name: text('name').notNull(),
  addedAt: integer('added_at').notNull(),
})

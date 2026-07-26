// Persistent file references + thumbnail cache — the "photos on disk" leg.
//
// Full-res images stay on the user's disk. We persist, per photo (keyed by its SQLite
// rowid), two things in IndexedDB:
//   - a FileSystemFileHandle — a re-openable *reference* to the original file (Chromium
//     only; structured-cloneable, so it survives reload). Re-granting read permission
//     needs a user gesture, so full-res is resolved lazily on click.
//   - a small JPEG thumbnail Blob — shown instantly in the gallery with no permission,
//     so a restored library is browsable before (or without) reconnecting the originals.
//
// Image *bytes* deliberately never go into SQLite: store.ts snapshots the whole DB to
// IndexedDB on every change, so bytes there would balloon every save.

const DB = 'photobook-files'
const HANDLES = 'handles'
const THUMBS = 'thumbs'

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1)
    req.onupgradeneeded = () => {
      const d = req.result
      if (!d.objectStoreNames.contains(HANDLES)) d.createObjectStore(HANDLES)
      if (!d.objectStoreNames.contains(THUMBS)) d.createObjectStore(THUMBS)
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}
function req<T>(store: string, mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest): Promise<T> {
  return open().then(
    (d) =>
      new Promise<T>((resolve, reject) => {
        const r = run(d.transaction(store, mode).objectStore(store))
        r.onsuccess = () => resolve(r.result as T)
        r.onerror = () => reject(r.error)
      }),
  )
}

/** Persist a photo's on-disk handle (if any) + its thumbnail, keyed by SQLite id. */
export async function putRef(id: number, handle: FileSystemFileHandle | null, thumb: Blob): Promise<void> {
  if (handle) await req(HANDLES, 'readwrite', (s) => s.put(handle, id))
  await req<IDBValidKey>(THUMBS, 'readwrite', (s) => s.put(thumb, id))
}

export function getThumb(id: number): Promise<Blob | undefined> {
  return req(THUMBS, 'readonly', (s) => s.get(id))
}

function getHandle(id: number): Promise<FileSystemFileHandle | undefined> {
  return req(HANDLES, 'readonly', (s) => s.get(id))
}

export async function hasHandle(id: number): Promise<boolean> {
  return (await getHandle(id)) != null
}

/** Drop a photo's stored handle + thumbnail. Only removes our *reference* — the file the
 *  handle points to stays untouched on the user's disk. */
export async function deleteRef(id: number): Promise<void> {
  await req(HANDLES, 'readwrite', (s) => s.delete(id))
  await req(THUMBS, 'readwrite', (s) => s.delete(id))
}

/** Downscale an image blob to a JPEG thumbnail (longest side ≤ max). */
export async function makeThumbnail(src: Blob, max = 256): Promise<Blob> {
  const bmp = await createImageBitmap(src)
  const scale = Math.min(1, max / Math.max(bmp.width, bmp.height))
  const w = Math.max(1, Math.round(bmp.width * scale))
  const h = Math.max(1, Math.round(bmp.height * scale))
  const canvas = new OffscreenCanvas(w, h)
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    bmp.close()
    throw new Error('OffscreenCanvas 2d context unavailable')
  }
  ctx.drawImage(bmp, 0, 0, w, h)
  bmp.close()
  return canvas.convertToBlob({ type: 'image/jpeg', quality: 0.82 })
}

/** Result of resolving a photo's full-res file from its persisted handle. */
export type ResolveResult =
  | { status: 'ok'; blob: Blob }
  | { status: 'none' } // no handle stored (e.g. added via the <input> fallback)
  | { status: 'denied' } // handle exists but read permission was not granted
  | { status: 'missing' } // handle exists but the file moved/was deleted

/**
 * Resolve the full-res file from its on-disk handle, re-granting read permission if needed.
 * `requestPermission` needs a user gesture, so call this from a click handler.
 */
export async function resolveFullRes(id: number): Promise<ResolveResult> {
  const handle = await getHandle(id)
  if (!handle) return { status: 'none' }
  const desc: FileSystemHandlePermissionDescriptor = { mode: 'read' }
  let perm = (await handle.queryPermission?.(desc)) ?? 'granted'
  if (perm !== 'granted') perm = (await handle.requestPermission?.(desc)) ?? 'denied'
  if (perm !== 'granted') return { status: 'denied' }
  try {
    return { status: 'ok', blob: await handle.getFile() }
  } catch {
    return { status: 'missing' } // NotFoundError — the original was moved or deleted
  }
}

<script lang="ts">
  import { onMount } from 'svelte'
  import { loadClip, embedImage, embeddingDim, embeddingBackend } from './lib/clip'
  import { webgpuUsable } from './lib/gpu'
  import {
    openStore,
    getByHash,
    putEmbedding,
    knn,
    count,
    hashBlob,
    listPhotos,
    getEmbedding,
    deletePhoto,
  } from './lib/store'
  import { putRef, getThumb, makeThumbnail, resolveFullRes, deleteRef } from './lib/files'
  import { createViewer, type Viewer } from './lib/viewer'

  type Photo = {
    id: number
    dbId?: number
    name: string
    thumbUrl: string // gallery <img> — small JPEG, always available (no permission)
    blob?: Blob // full-res, once added this session or resolved from disk
    hash?: string
    embedding?: Float32Array
    cached?: boolean
    missing?: boolean // disk reference broken / read permission denied
    busy: boolean
    stage?: string // what's happening while busy (shown in the dot's tooltip)
  }

  type Incoming = { file: File; handle: FileSystemFileHandle | null }

  let photos = $state<Photo[]>([])
  let selectedId = $state<number | null>(null)
  let highlighted = $state<Set<number>>(new Set())
  let exposure = $state(0)
  let status = $state('booting…')
  let ready = $state(false)
  let dim = $state(512)
  let clipBackend = $state('')
  let viewerBackend = $state('')
  let gpuOk = $state(false)
  let dragOver = $state(false)
  let persistent = $state(false)
  let tipId = $state<number | null>(null) // gallery id whose processing tooltip is pinned open

  let nextId = 0
  let canvasEl: HTMLCanvasElement
  let fileInput: HTMLInputElement
  let viewer: Viewer | null = null

  const selected = $derived(photos.find((p) => p.id === selectedId) ?? null)

  onMount(async () => {
    gpuOk = await webgpuUsable()
    dim = embeddingDim()
    const store = await openStore(dim)
    persistent = store.persistent
    viewer = await createViewer(canvasEl)
    viewerBackend = viewer.backend
    await restoreGallery()
    status = 'loading CLIP model…'
    await loadClip((m) => (status = m))
    clipBackend = embeddingBackend()
    ready = true
    status = persistent
      ? `ready · ${photos.length} photo${photos.length === 1 ? '' : 's'} restored`
      : 'ready · in-memory (no persistence)'
  })

  // Repopulate the gallery from the persisted DB + cached thumbnails — no originals or
  // permissions needed. Full-res loads lazily from disk when a photo is opened.
  async function restoreGallery() {
    for (const row of listPhotos()) {
      const thumb = await getThumb(row.id)
      if (!thumb) continue // no cached thumbnail — can't show it, skip
      photos.push({
        id: nextId++,
        dbId: row.id,
        name: row.name,
        thumbUrl: URL.createObjectURL(thumb),
        busy: false,
      })
    }
  }

  // Open the file picker. `showOpenFilePicker` yields persistent handles (references to
  // the originals on disk); the <input> fallback yields bytes only (no disk reference).
  async function pickFiles() {
    if (window.showOpenFilePicker) {
      let handles: FileSystemFileHandle[]
      try {
        handles = await window.showOpenFilePicker({
          multiple: true,
          types: [
            { description: 'Images', accept: { 'image/*': ['.jpg', '.jpeg', '.png', '.webp', '.avif', '.gif'] } },
          ],
        })
      } catch {
        return // user cancelled
      }
      await ingest(await Promise.all(handles.map(async (h) => ({ file: await h.getFile(), handle: h }))))
    } else {
      fileInput.click()
    }
  }

  function onInputChange(e: Event) {
    const files = (e.currentTarget as HTMLInputElement).files
    if (files) void ingest(Array.from(files).map((file) => ({ file, handle: null })))
  }

  async function ingest(items: Incoming[]) {
    for (const item of items) {
      if (!item.file.type.startsWith('image/')) continue
      // full-res blob is on hand this session; thumbUrl fills in a moment
      photos.push({ id: nextId++, name: item.file.name, thumbUrl: '', blob: item.file, busy: true })
      // Mutate the *proxied* element Svelte stored, not the plain literal — writes to the
      // local object wouldn't be tracked by $state's deep reactivity.
      void process(photos[photos.length - 1], item)
    }
  }

  // Thumbnail + embed (or cache-hit) + persist the disk reference — off the critical path.
  // `p.stage` drives the tooltip on the busy dot.
  async function process(p: Photo, { file, handle }: Incoming) {
    try {
      p.stage = 'making thumbnail'
      const thumb = await makeThumbnail(file)
      p.thumbUrl = URL.createObjectURL(thumb)
      if (selectedId === null) void select(p)
      p.stage = 'hashing'
      const hash = await hashBlob(file)
      p.hash = hash
      p.stage = 'checking cache'
      const hit = await getByHash(hash)
      if (hit) {
        p.embedding = hit.embedding
        p.dbId = hit.id
        p.cached = true
      } else {
        p.stage = 'embedding (CLIP)'
        const emb = await embedImage(file)
        p.stage = 'storing vector'
        p.dbId = await putEmbedding(hash, file.name, emb)
        p.embedding = emb
      }
      p.stage = 'saving reference'
      await putRef(p.dbId, handle, thumb) // persist handle (if any) + thumbnail by db id
      p.busy = false
      p.stage = undefined
      status = `${count()} photos (${dim}-d, ${clipBackend})${handle ? '' : ' · no disk ref'}`
    } catch (err) {
      p.busy = false
      p.stage = undefined
      status = `add failed: ${err}`
      console.error(err)
    }
  }

  async function select(photo: Photo) {
    selectedId = photo.id
    viewer?.setExposure(exposure)
    if (photo.blob) {
      viewer?.setImage(photo.blob)
      return
    }
    // Restored photo: resolve full-res from its on-disk handle (may prompt — this is a click).
    if (photo.dbId != null) {
      const res = await resolveFullRes(photo.dbId)
      if (res.status === 'ok') {
        photo.blob = res.blob
        photo.missing = false
        viewer?.setImage(res.blob)
        return
      }
      photo.missing = res.status === 'missing' || res.status === 'denied'
      status =
        res.status === 'denied'
          ? 'permission needed to open the original — click the photo again to grant'
          : res.status === 'missing'
            ? `“${photo.name}” was moved or deleted on disk`
            : `“${photo.name}” has no saved disk reference (added via fallback)`
    }
    // Fallback so the viewer shows something: the thumbnail.
    viewer?.setImage(await (await fetch(photo.thumbUrl)).blob())
  }

  function onExposure(e: Event) {
    exposure = Number((e.target as HTMLInputElement).value)
    viewer?.setExposure(exposure)
  }

  function findSimilar() {
    if (!selected) return
    // Restored photos carry no embedding until needed — lazy-load it from the store.
    let emb = selected.embedding
    if (!emb && selected.dbId != null) {
      emb = getEmbedding(selected.dbId) ?? undefined
      selected.embedding = emb
    }
    if (!emb) return
    // KNN returns DB rowids; map them back to the gallery photos that carry them.
    const rows = knn(emb, 7) // +1 for the query itself
    const set = new Set<number>()
    for (const { id } of rows) {
      const gp = photos.find((p) => p.dbId === id)
      if (gp && gp.id !== selected.id) set.add(gp.id)
    }
    highlighted = set
    status = `nearest ${set.size} to #${selected.id}`
  }

  function clearHighlights() {
    highlighted = new Set()
  }

  // Remove a photo from the gallery: delete its SQLite row + vector and its IndexedDB
  // handle/thumbnail. The original file on disk is left untouched — we only drop our
  // reference to it.
  async function removePhoto(photo: Photo) {
    if (photo.dbId != null) {
      deletePhoto(photo.dbId)
      await deleteRef(photo.dbId)
    }
    URL.revokeObjectURL(photo.thumbUrl)
    photos = photos.filter((p) => p.id !== photo.id)
    if (highlighted.has(photo.id)) highlighted = new Set([...highlighted].filter((id) => id !== photo.id))
    if (selectedId === photo.id) selectedId = null
    status = `removed “${photo.name}” · ${count()} photos`
  }

  async function onDrop(e: DragEvent) {
    e.preventDefault()
    dragOver = false
    const items = e.dataTransfer?.items
    if (!items) return
    // DataTransferItems are only valid during the event — kick off handle/file reads
    // synchronously, then await. getAsFileSystemHandle gives a persistent disk reference.
    const pending = Array.from(items)
      .filter((it) => it.kind === 'file')
      .map((it) => ({ file: it.getAsFile(), handleP: it.getAsFileSystemHandle?.() ?? Promise.resolve(null) }))
    const out: Incoming[] = []
    for (const { file, handleP } of pending) {
      const h = await handleP
      if (file) out.push({ file, handle: h && h.kind === 'file' ? (h as FileSystemFileHandle) : null })
    }
    await ingest(out)
  }
</script>

<div class="flex h-screen flex-col bg-background text-foreground">
  <!-- Header / status -->
  <header class="flex items-center gap-4 border-b px-5 py-3">
    <h1 class="text-sm font-semibold tracking-tight">rust-client · photo spike</h1>
    <span class="text-xs text-muted-foreground">{status}</span>
    <div class="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
      <span class="rounded-md bg-secondary px-2 py-1">CLIP: {clipBackend || '…'}</span>
      <span class="rounded-md bg-secondary px-2 py-1">viewer: {viewerBackend || '…'}</span>
      <span class="rounded-md bg-secondary px-2 py-1">WebGPU: {gpuOk ? 'yes' : 'no'}</span>
      <span class="rounded-md bg-secondary px-2 py-1">store: {persistent ? 'sqlite-vec · IndexedDB' : 'sqlite-vec · memory'}</span>
    </div>
  </header>

  <main class="flex min-h-0 flex-1">
    <!-- Viewer pane (Rust canvas once wgpu backend lands) -->
    <section class="flex min-w-0 flex-1 flex-col gap-3 p-5">
      <div class="relative min-h-0 flex-1 overflow-hidden rounded-lg border bg-card">
        <canvas bind:this={canvasEl} class="h-full w-full"></canvas>
        {#if !selected}
          <div class="pointer-events-none absolute inset-0 grid place-items-center text-sm text-muted-foreground">
            Drop photos or click “Add” to begin
          </div>
        {/if}
      </div>

      <!-- Controls -->
      <div class="flex items-center gap-4 rounded-lg border bg-card px-4 py-3">
        <label class="flex flex-1 items-center gap-3 text-xs text-muted-foreground">
          Exposure
          <input
            type="range"
            min="-3"
            max="3"
            step="0.05"
            value={exposure}
            oninput={onExposure}
            class="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-secondary accent-primary"
          />
          <span class="w-14 text-right tabular-nums text-foreground">{exposure.toFixed(2)} EV</span>
        </label>
        <button
          onclick={findSimilar}
          disabled={!selected || selected.busy}
          class="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
        >
          Find similar
        </button>
        {#if highlighted.size}
          <button onclick={clearHighlights} class="rounded-md border px-3 py-1.5 text-xs">Clear</button>
        {/if}
      </div>
    </section>

    <!-- Gallery (drop target) -->
    <aside
      class="flex w-80 flex-col border-l"
      class:ring-2={dragOver}
      role="region"
      aria-label="gallery"
      ondragover={(e) => {
        e.preventDefault()
        dragOver = true
      }}
      ondragleave={() => (dragOver = false)}
      ondrop={onDrop}
    >
      <div class="flex items-center justify-between border-b px-4 py-2.5">
        <span class="text-xs font-medium">Gallery · {photos.length}</span>
        <button
          onclick={pickFiles}
          disabled={!ready}
          class="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground disabled:opacity-40"
        >
          + Add
        </button>
        <input
          bind:this={fileInput}
          type="file"
          accept="image/*"
          multiple
          class="hidden"
          onchange={onInputChange}
        />
      </div>

      <div class="grid min-h-0 flex-1 auto-rows-min grid-cols-3 content-start gap-2 overflow-y-auto p-3">
        {#each photos as photo (photo.id)}
          <div
            class="group relative aspect-square overflow-hidden rounded-md border"
            class:ring-2={photo.id === selectedId}
            style={highlighted.has(photo.id)
              ? 'outline: 3px solid var(--color-highlight); outline-offset: 1px'
              : ''}
          >
            <button
              type="button"
              onclick={() => select(photo)}
              class="block h-full w-full"
              aria-label={`Open ${photo.name}`}
            >
              <img src={photo.thumbUrl} alt={photo.name} class="h-full w-full object-cover" />
            </button>

            <!-- Remove from gallery (keeps the file on disk). Appears on hover. -->
            <button
              type="button"
              onclick={(e) => {
                e.stopPropagation()
                void removePhoto(photo)
              }}
              class="absolute left-1 top-1 z-10 grid h-5 w-5 place-items-center rounded-full bg-background/70 text-sm leading-none opacity-0 transition-opacity hover:bg-destructive hover:text-destructive-foreground group-hover:opacity-100"
              aria-label={`Remove ${photo.name} from gallery`}
              title="Remove from gallery (keeps the file on disk)"
            >
              ×
            </button>

            {#if photo.busy}
              <!-- Twinkling status dot; hover or click reveals the current stage. -->
              <button
                type="button"
                onclick={(e) => {
                  e.stopPropagation()
                  tipId = tipId === photo.id ? null : photo.id
                }}
                class="group/dot absolute right-1 top-1 z-10"
                aria-label={`Processing ${photo.name}: ${photo.stage ?? 'working'}`}
              >
                <span
                  class="block h-2.5 w-2.5 animate-pulse rounded-full bg-orange-500 shadow-[0_0_7px_2px] shadow-orange-400/70"
                ></span>
                <span
                  class="pointer-events-none absolute right-0 top-4 z-20 whitespace-nowrap rounded bg-popover px-1.5 py-0.5 text-[9px] text-popover-foreground shadow-md ring-1 ring-border transition-opacity group-hover/dot:opacity-100"
                  class:opacity-0={tipId !== photo.id}
                >
                  {photo.stage ?? 'processing…'}
                </span>
              </button>
            {:else if photo.missing}
              <div
                class="pointer-events-none absolute inset-x-0 bottom-0 bg-destructive/80 py-0.5 text-center text-[9px] text-destructive-foreground"
                title="Original moved/deleted or access not granted"
              >
                offline
              </div>
            {/if}
          </div>
        {/each}
        {#if !photos.length}
          <p class="col-span-3 mt-8 text-center text-xs text-muted-foreground">
            No photos yet.<br />Drop images here.
          </p>
        {/if}
      </div>
    </aside>
  </main>
</div>

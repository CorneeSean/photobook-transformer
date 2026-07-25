<script lang="ts">
  import { onMount } from 'svelte'
  import { loadClip, embedImage, embeddingDim, embeddingBackend, hasWebGPU } from './lib/clip'
  import { initStore, insert, knn, count } from './lib/store'
  import { createViewer, type Viewer } from './lib/viewer'

  type Photo = {
    id: number
    url: string
    blob: Blob
    embedding?: Float32Array
    busy: boolean
  }

  let photos = $state<Photo[]>([])
  let selectedId = $state<number | null>(null)
  let highlighted = $state<Set<number>>(new Set())
  let exposure = $state(0)
  let status = $state('booting…')
  let ready = $state(false)
  let dim = $state(512)
  let clipBackend = $state('')
  let viewerBackend = $state('')
  let dragOver = $state(false)

  let nextId = 0
  let canvasEl: HTMLCanvasElement
  let fileInput: HTMLInputElement
  let viewer: Viewer | null = null

  const selected = $derived(photos.find((p) => p.id === selectedId) ?? null)

  onMount(async () => {
    dim = embeddingDim()
    await initStore(dim)
    viewer = await createViewer(canvasEl)
    viewerBackend = viewer.backend
    status = 'loading CLIP model…'
    await loadClip((m) => (status = m))
    clipBackend = embeddingBackend()
    ready = true
    status = 'ready'
  })

  async function addFiles(files: FileList | File[]) {
    for (const f of Array.from(files)) {
      if (!f.type.startsWith('image/')) continue
      const url = URL.createObjectURL(f)
      photos.push({ id: nextId++, url, blob: f, busy: true })
      // Mutate the *proxied* element Svelte stored, not the plain literal — writes
      // to the local object wouldn't be tracked by $state's deep reactivity.
      const p = photos[photos.length - 1]
      if (selectedId === null) select(p)
      // Embed off the main thread's critical path; update in place when done.
      embedImage(f)
        .then((emb) => {
          p.embedding = emb
          insert(p.id, emb)
          p.busy = false
          status = `${count()} / ${photos.length} embedded (${dim}-d, ${clipBackend})`
        })
        .catch((err) => {
          p.busy = false
          status = `embed failed: ${err}`
          console.error(err)
        })
    }
  }

  function select(photo: Photo) {
    selectedId = photo.id
    viewer?.setImage(photo.blob)
    viewer?.setExposure(exposure)
  }

  function onExposure(e: Event) {
    exposure = Number((e.target as HTMLInputElement).value)
    viewer?.setExposure(exposure)
  }

  function findSimilar() {
    if (!selected?.embedding) return
    const ids = knn(selected.embedding, 6)
    const set = new Set<number>(Array.from(ids))
    set.delete(selected.id) // don't highlight the query itself
    highlighted = set
    status = `nearest ${set.size} to #${selected.id}`
  }

  function clearHighlights() {
    highlighted = new Set()
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    dragOver = false
    if (e.dataTransfer?.files.length) addFiles(e.dataTransfer.files)
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
      <span class="rounded-md bg-secondary px-2 py-1">WebGPU: {hasWebGPU() ? 'yes' : 'no'}</span>
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
          disabled={!selected?.embedding}
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
          onclick={() => fileInput.click()}
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
          onchange={(e) => e.currentTarget.files && addFiles(e.currentTarget.files)}
        />
      </div>

      <div class="grid min-h-0 flex-1 auto-rows-min grid-cols-3 content-start gap-2 overflow-y-auto p-3">
        {#each photos as photo (photo.id)}
          <button
            onclick={() => select(photo)}
            class="group relative aspect-square overflow-hidden rounded-md border"
            class:ring-2={photo.id === selectedId}
            style={highlighted.has(photo.id)
              ? 'outline: 3px solid var(--color-highlight); outline-offset: 1px'
              : ''}
          >
            <img src={photo.url} alt="" class="h-full w-full object-cover" />
            {#if photo.busy}
              <div
                class="absolute inset-0 grid place-items-center bg-background/60 text-[10px] text-muted-foreground"
              >
                embedding…
              </div>
            {/if}
          </button>
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

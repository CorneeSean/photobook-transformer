<script lang="ts">
  import { onMount } from 'svelte'
  import initRawPdf, { raw_info, raw_to_pdf, develop } from './wasm/rawpdf/rust_client_rawpdf'

  let ready = $state(false)
  let status = $state('loading wasm…')
  let info = $state('')
  let pdfUrl = $state<string | null>(null)
  let pdfName = $state('page.pdf')
  let busy = $state(false)
  let canvasEl: HTMLCanvasElement
  let fileInput: HTMLInputElement

  onMount(async () => {
    await initRawPdf()
    ready = true
    status = 'Drop a camera RAW (DNG/CR2/NEF/ARW/…) or pick one.'
  })

  async function handle(file: File) {
    if (!ready || busy) return
    busy = true
    status = `decoding ${file.name}…`
    if (pdfUrl) {
      URL.revokeObjectURL(pdfUrl)
      pdfUrl = null
    }
    try {
      const buf = new Uint8Array(await file.arrayBuffer())
      info = raw_info(buf) // "WxH · make model · cfa"
      // Develop → RGBA, draw to canvas (visual proof of decode).
      const d = develop(buf)
      canvasEl.width = d.width
      canvasEl.height = d.height
      canvasEl
        .getContext('2d')!
        .putImageData(new ImageData(new Uint8ClampedArray(d.rgba), d.width, d.height), 0, 0)
      // Assemble the one-page PDF, offer it for download / preview.
      const pdf = raw_to_pdf(buf)
      pdfName = file.name.replace(/\.[^.]+$/, '') + '.pdf'
      pdfUrl = URL.createObjectURL(new Blob([pdf as BlobPart], { type: 'application/pdf' }))
      status = `${info}  →  PDF ${(pdf.length / 1e6).toFixed(2)} MB`
    } catch (e) {
      status = `error: ${e}`
      console.error(e)
    } finally {
      busy = false
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    const f = e.dataTransfer?.files[0]
    if (f) handle(f)
  }
</script>

<div class="mx-auto flex min-h-screen max-w-4xl flex-col gap-4 p-6">
  <header class="flex items-baseline gap-3">
    <h1 class="text-base font-semibold">RAW → PDF</h1>
    <span class="text-xs text-muted-foreground">
      decode + develop + assemble a PDF, 100% in the browser (rawloader + printpdf, WASM)
    </span>
  </header>

  <div
    role="region"
    aria-label="drop zone"
    ondragover={(e) => e.preventDefault()}
    ondrop={onDrop}
    class="flex items-center gap-3 rounded-lg border border-dashed bg-card px-4 py-3 text-sm"
  >
    <button
      onclick={() => fileInput.click()}
      disabled={!ready || busy}
      class="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
    >
      Pick RAW
    </button>
    <input
      bind:this={fileInput}
      type="file"
      class="hidden"
      onchange={(e) => e.currentTarget.files?.[0] && handle(e.currentTarget.files[0])}
    />
    <span class="text-muted-foreground">{status}</span>
    {#if pdfUrl}
      <a
        href={pdfUrl}
        download={pdfName}
        class="ml-auto rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-secondary"
      >
        Download PDF
      </a>
    {/if}
  </div>

  <!-- Developed image (visual proof of the decode) -->
  <div class="grid flex-1 place-items-center overflow-hidden rounded-lg border bg-card p-2">
    <canvas bind:this={canvasEl} class="max-h-[55vh] max-w-full object-contain"></canvas>
  </div>

  <!-- Inline PDF preview -->
  {#if pdfUrl}
    <iframe title="generated PDF" src={pdfUrl} class="h-[70vh] w-full rounded-lg border"></iframe>
  {/if}
</div>

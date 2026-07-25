// The "pixels" leg of the spike: a photo viewer that owns a <canvas> and applies a
// live exposure adjustment driven by the shell's slider.
//
// R1 sequencing: this Canvas2D implementation exists so the app runs end-to-end
// while steps 1–3 (shell + CLIP + Rust store) are validated. It is then swapped for
// the Rust/wgpu backend (`./wasm/viewer`) behind this exact `Viewer` interface —
// the shell code doesn't change, only which `createViewer` is imported.

export interface Viewer {
  /** Load an image (object URL or Blob) and fit it to the canvas. */
  setImage(src: string | Blob): Promise<void>
  /** Exposure in stops; brightness multiplier = 2**stops. Live, cheap to call. */
  setExposure(stops: number): void
  /** Which backend is actually running (shown in the UI). */
  readonly backend: 'canvas2d' | 'wgpu'
}

class Canvas2DViewer implements Viewer {
  readonly backend = 'canvas2d' as const
  private ctx: CanvasRenderingContext2D
  private bitmap: ImageBitmap | null = null
  private exposure = 0

  constructor(private canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('2d context unavailable')
    this.ctx = ctx
  }

  async setImage(src: string | Blob): Promise<void> {
    const blob = src instanceof Blob ? src : await (await fetch(src)).blob()
    this.bitmap?.close()
    this.bitmap = await createImageBitmap(blob)
    this.draw()
  }

  setExposure(stops: number): void {
    this.exposure = stops
    this.draw()
  }

  private draw(): void {
    const { canvas, ctx, bitmap } = this
    const dpr = window.devicePixelRatio || 1
    const cssW = canvas.clientWidth || 640
    const cssH = canvas.clientHeight || 480
    canvas.width = Math.round(cssW * dpr)
    canvas.height = Math.round(cssH * dpr)
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    if (!bitmap) return
    // Contain-fit the image in the canvas.
    const scale = Math.min(canvas.width / bitmap.width, canvas.height / bitmap.height)
    const w = bitmap.width * scale
    const h = bitmap.height * scale
    const x = (canvas.width - w) / 2
    const y = (canvas.height - h) / 2
    ctx.filter = `brightness(${2 ** this.exposure})`
    ctx.drawImage(bitmap, x, y, w, h)
    ctx.filter = 'none'
  }
}

/** Create the currently-active viewer for a canvas. Swap the body to the wgpu
 *  backend once `./wasm/viewer` is built (same signature, returns a `Viewer`). */
export async function createViewer(canvas: HTMLCanvasElement): Promise<Viewer> {
  return new Canvas2DViewer(canvas)
}

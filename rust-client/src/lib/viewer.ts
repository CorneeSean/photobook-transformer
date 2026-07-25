// The "pixels" leg of the spike: a photo viewer that owns a <canvas> and applies a
// live exposure adjustment driven by the shell's slider.
//
// Two backends behind one interface: the Rust/wgpu viewer (`../wasm/viewer`) owns
// the canvas via WebGPU and applies exposure in a fragment shader; the Canvas2D
// viewer is the fallback when WebGPU is unavailable (or GPU init fails). The shell
// (App.svelte) only ever talks to the `Viewer` interface.

import initViewer, { create_viewer, type PhotoViewer } from '../wasm/viewer/rust_client_viewer'

export interface Viewer {
  /** Load an image (object URL or Blob) and fit it to the canvas. */
  setImage(src: string | Blob): Promise<void>
  /** Exposure in stops; brightness multiplier = 2**stops. Live, cheap to call. */
  setExposure(stops: number): void
  /** Which backend is actually running (shown in the UI): 'webgpu' | 'webgl2' | 'canvas2d'. */
  readonly backend: string
}

class Canvas2DViewer implements Viewer {
  readonly backend = 'canvas2d' as const
  // Null if the canvas already holds another context (e.g. a failed wgpu attempt
  // claimed 'webgpu'); the viewer then simply no-ops rather than crashing the app.
  private ctx: CanvasRenderingContext2D | null
  private bitmap: ImageBitmap | null = null
  private exposure = 0

  constructor(private canvas: HTMLCanvasElement) {
    this.ctx = canvas.getContext('2d')
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
    if (!ctx) return
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

// ---- wgpu backend ----------------------------------------------------------

const MAX_TEX = 2048 // cap the uploaded texture's longest side (memory + upload cost)

/** Decode an image source to RGBA8 bytes (downscaled to fit MAX_TEX). */
async function decodeToRGBA(
  src: string | Blob,
): Promise<{ data: Uint8Array; width: number; height: number }> {
  const blob = src instanceof Blob ? src : await (await fetch(src)).blob()
  const bmp = await createImageBitmap(blob)
  const scale = Math.min(1, MAX_TEX / Math.max(bmp.width, bmp.height))
  const width = Math.max(1, Math.round(bmp.width * scale))
  const height = Math.max(1, Math.round(bmp.height * scale))
  const off = new OffscreenCanvas(width, height)
  const ctx = off.getContext('2d')
  if (!ctx) throw new Error('OffscreenCanvas 2d context unavailable')
  ctx.drawImage(bmp, 0, 0, width, height)
  bmp.close()
  const { data } = ctx.getImageData(0, 0, width, height)
  return { data: new Uint8Array(data.buffer), width, height }
}

function srgbToLinear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}

/** Read an element's background as linear RGB (0..1) for use as the wgpu clear colour.
 *  The surface is sRGB, so the clear value is treated as linear — hence the convert.
 *  getComputedStyle may return the colour in any CSS Color 4 form (our theme uses
 *  oklch), so we rasterize it via a 1×1 2D canvas to get normalized sRGB bytes. */
function backgroundLinear(el: Element): [number, number, number] {
  const color = getComputedStyle(el).backgroundColor
  const c = document.createElement('canvas')
  c.width = c.height = 1
  const ctx = c.getContext('2d')
  if (!ctx) return [1, 1, 1]
  ctx.fillStyle = color
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
  if (a === 0) return [1, 1, 1] // transparent → default to white
  return [srgbToLinear(r / 255), srgbToLinear(g / 255), srgbToLinear(b / 255)]
}

/** Physical backing size for a canvas, DPR-aware but capped so the surface stays sane. */
function backingSize(canvas: HTMLCanvasElement): [number, number] {
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  return [
    Math.max(1, Math.round(canvas.clientWidth * dpr)),
    Math.max(1, Math.round(canvas.clientHeight * dpr)),
  ]
}

class WgpuViewer implements Viewer {
  readonly backend: string // 'webgpu' or 'webgl2', from the Rust adapter
  private ro: ResizeObserver

  constructor(
    private canvas: HTMLCanvasElement,
    private pv: PhotoViewer,
  ) {
    this.backend = pv.backend()
    this.ro = new ResizeObserver(() => this.syncSize())
    this.ro.observe(canvas)
  }

  private syncSize(): void {
    const [w, h] = backingSize(this.canvas)
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w
      this.canvas.height = h
    }
    this.pv.resize(w, h)
  }

  async setImage(src: string | Blob): Promise<void> {
    const { data, width, height } = await decodeToRGBA(src)
    this.pv.set_image(data, width, height)
  }

  setExposure(stops: number): void {
    this.pv.set_exposure(stops)
  }
}

let viewerModuleReady: Promise<void> | null = null

/**
 * Create the best available viewer for a canvas: Rust/wgpu when WebGPU is present,
 * else the Canvas2D fallback. Returns the `Viewer` interface either way.
 */
export async function createViewer(canvas: HTMLCanvasElement): Promise<Viewer> {
  // The wgpu viewer covers WebGPU *and* WebGL2, so try it unconditionally — no need
  // to probe first. Canvas2D is only a last resort for browsers with neither (near
  // extinct). If wgpu init fails it may leave a GPU context on the canvas, making the
  // Canvas2D fallback a no-op — acceptable in that no-WebGPU-and-no-WebGL2 case.
  try {
    if (!viewerModuleReady) viewerModuleReady = initViewer().then(() => {})
    await viewerModuleReady
    // The surface is created from the canvas, so it needs a backing size first.
    const [w, h] = backingSize(canvas)
    canvas.width = w
    canvas.height = h
    const pv = await create_viewer(canvas)
    // Match the viewer's letterbox to the page's card background (theme-aware).
    const [r, g, b] = backgroundLinear(canvas.parentElement ?? canvas)
    pv.set_background(r, g, b)
    return new WgpuViewer(canvas, pv)
  } catch (err) {
    console.warn('[viewer] wgpu (WebGPU/WebGL2) init failed, falling back to Canvas2D', err)
  }
  return new Canvas2DViewer(canvas)
}

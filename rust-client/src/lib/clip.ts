// CLIP image embeddings via transformers.js, running in the browser on WebGPU
// (falling back to WASM). This is the "model" leg of the spike — the one piece we
// deliberately keep in JS because transformers.js already packages the model +
// pre/post-processing. Everything downstream (store, viewer) is Rust/WASM.
//
// Default model: Xenova/clip-vit-base-patch32 -> 512-d image embeddings (small,
// fast first-load). Toggle CLIP_MODEL to a ViT-L/14 variant for 768-d parity with
// the server pipeline's pgvector column (pipeline/src/album/db/models.py:139).

import {
  AutoProcessor,
  CLIPVisionModelWithProjection,
  RawImage,
  type Processor,
  type PreTrainedModel,
} from '@huggingface/transformers'
import { webgpuUsable } from './gpu'

// Spike default (R3): 512-d. For 768-d parity use 'Xenova/clip-vit-large-patch14'.
const CLIP_MODEL = 'Xenova/clip-vit-base-patch32'

export type Backend = 'webgpu' | 'wasm'

let processor: Processor | null = null
let model: PreTrainedModel | null = null
let backend: Backend = 'wasm'
let loading: Promise<void> | null = null

export function embeddingBackend(): Backend {
  return backend
}

/** Load the CLIP model once. `onProgress` receives transformers.js download events. */
export function loadClip(onProgress?: (msg: string) => void): Promise<void> {
  if (loading) return loading
  loading = (async () => {
    backend = (await webgpuUsable()) ? 'webgpu' : 'wasm'
    onProgress?.(`loading ${CLIP_MODEL} on ${backend}…`)
    processor = await AutoProcessor.from_pretrained(CLIP_MODEL)
    try {
      model = await CLIPVisionModelWithProjection.from_pretrained(CLIP_MODEL, {
        device: backend,
        dtype: 'fp32',
      })
    } catch (err) {
      // WebGPU init can fail on some drivers — fall back to WASM rather than dying.
      console.warn('[clip] webgpu load failed, falling back to wasm', err)
      backend = 'wasm'
      model = await CLIPVisionModelWithProjection.from_pretrained(CLIP_MODEL, {
        device: 'wasm',
        dtype: 'fp32',
      })
    }
    onProgress?.(`ready (${backend})`)
  })()
  return loading
}

/**
 * Embed one image into an L2-normalized Float32Array (unit vector, so a dot
 * product == cosine similarity — which is what the Rust KNN store expects).
 */
export async function embedImage(src: string | Blob): Promise<Float32Array> {
  if (!processor || !model) await loadClip()
  if (!processor || !model) throw new Error('CLIP failed to load')

  const image = src instanceof Blob ? await RawImage.fromBlob(src) : await RawImage.read(src)
  const inputs = await processor(image)
  const { image_embeds } = await model(inputs)
  const normed = image_embeds.normalize(2, -1)
  const arr = normed.tolist()[0] as number[]
  return Float32Array.from(arr)
}

export function embeddingDim(): number {
  return CLIP_MODEL.includes('large') ? 768 : 512
}

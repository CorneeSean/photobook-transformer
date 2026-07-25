//! GPU photo viewer (wgpu) — step 4 of the spike.
//!
//! Placeholder: the Canvas2D viewer in `src/lib/viewer.ts` currently drives the UI
//! so the app runs end-to-end (R1 sequencing). The wgpu backend — create a surface
//! from the shell's <canvas>, upload the image as a texture, apply exposure in a
//! fragment shader — lands here and is swapped in behind the same `Viewer` interface.

use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn viewer_placeholder() -> String {
    "wgpu viewer not yet built".to_string()
}

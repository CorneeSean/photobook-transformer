//! Proof-of-concept: camera RAW → developed image → PDF, entirely client-side in WASM.
//!
//! Not a real developer — a crude half-resolution demosaic just good enough to prove
//! the two hard links in a browser: (1) rawloader decodes a RAW in wasm32, and
//! (3) printpdf assembles a one-page PDF with the developed image embedded. A
//! production path would use imagepipe/rawler for demosaic and moxcms for colour.

use std::io::Cursor;
use wasm_bindgen::prelude::*;

/// Decode a RAW from memory and return a short description (proves rawloader in WASM).
#[wasm_bindgen]
pub fn raw_info(bytes: &[u8]) -> Result<String, JsValue> {
    console_error_panic_hook::set_once();
    let mut cursor = Cursor::new(bytes);
    let raw = rawloader::decode(&mut cursor)
        .map_err(|e| JsValue::from_str(&format!("rawloader decode failed: {e:?}")))?;
    Ok(format!(
        "{}x{} · {} {} · cfa {}",
        raw.width,
        raw.height,
        raw.make,
        raw.model,
        raw.cropped_cfa().name
    ))
}

/// A developed image handed back to JS for canvas display (visual proof of decode).
#[wasm_bindgen]
pub struct Developed {
    pub width: usize,
    pub height: usize,
    rgba: Vec<u8>,
}

#[wasm_bindgen]
impl Developed {
    #[wasm_bindgen(getter)]
    pub fn rgba(&self) -> Vec<u8> {
        self.rgba.clone()
    }
}

/// Decode a RAW and develop it to RGBA8 (for drawing to a `<canvas>`).
#[wasm_bindgen]
pub fn develop(bytes: &[u8]) -> Result<Developed, JsValue> {
    console_error_panic_hook::set_once();
    let mut cursor = Cursor::new(bytes);
    let raw = rawloader::decode(&mut cursor)
        .map_err(|e| JsValue::from_str(&format!("rawloader decode failed: {e:?}")))?;
    let mosaic = match &raw.data {
        rawloader::RawImageData::Integer(d) => d,
        rawloader::RawImageData::Float(_) => {
            return Err(JsValue::from_str("float RAW not supported in this PoC"));
        }
    };
    let (rgb, ow, oh) = demosaic_halfres(mosaic, raw.width, raw.height, &raw);
    let mut rgba = Vec::with_capacity(ow * oh * 4);
    for px in rgb.chunks_exact(3) {
        rgba.extend_from_slice(px);
        rgba.push(255);
    }
    Ok(Developed {
        width: ow,
        height: oh,
        rgba,
    })
}

/// Full slice: decode a RAW, develop it crudely to RGB8, and return a one-page PDF.
#[wasm_bindgen]
pub fn raw_to_pdf(bytes: &[u8]) -> Result<Vec<u8>, JsValue> {
    console_error_panic_hook::set_once();

    // --- 1. decode ---------------------------------------------------------
    let mut cursor = Cursor::new(bytes);
    let raw = rawloader::decode(&mut cursor)
        .map_err(|e| JsValue::from_str(&format!("rawloader decode failed: {e:?}")))?;
    let (w, h) = (raw.width, raw.height);
    let mosaic = match &raw.data {
        rawloader::RawImageData::Integer(d) => d,
        rawloader::RawImageData::Float(_) => {
            return Err(JsValue::from_str("float RAW not supported in this PoC"));
        }
    };

    // --- 2. crude half-res demosaic → RGB8 ---------------------------------
    let (rgb, ow, oh) = demosaic_halfres(mosaic, w, h, &raw);

    // --- 3. assemble a one-page PDF with the image -------------------------
    use printpdf::*;
    let image = RawImage {
        width: ow,
        height: oh,
        data_format: RawImageFormat::RGB8,
        pixels: RawImageData::U8(rgb),
        tag: Vec::new(),
    };
    let mut doc = PdfDocument::new("rawpdf proof");
    let image_id = doc.add_image(&image);

    // Page sized to the image at 300 DPI, image filling it.
    let dpi = 300.0_f32;
    let w_mm = ow as f32 / dpi * 25.4;
    let h_mm = oh as f32 / dpi * 25.4;
    let ops = vec![Op::UseXobject {
        id: image_id,
        transform: XObjectTransform {
            dpi: Some(dpi),
            ..Default::default()
        },
    }];
    let page = PdfPage::new(Mm(w_mm), Mm(h_mm), ops);

    let mut warnings = Vec::new();
    let pdf = doc
        .with_pages(vec![page])
        .save(&PdfSaveOptions::default(), &mut warnings);
    Ok(pdf)
}

/// Each 2×2 Bayer block → one RGB pixel. Black/white normalisation, camera white
/// balance, and a 1/2.2 gamma so the result is viewable. Output is (w/2)×(h/2).
/// White point is the actual data max (robust to bogus metadata white levels).
fn demosaic_halfres(
    mosaic: &[u16],
    w: usize,
    h: usize,
    raw: &rawloader::RawImage,
) -> (Vec<u8>, usize, usize) {
    let cfa = raw.cropped_cfa();
    let black = raw.blacklevels[0] as f32;
    let white = *mosaic.iter().max().unwrap_or(&1) as f32;
    let range = (white - black).max(1.0);
    let wb = raw.wb_coeffs; // [R, G, B, _]
    let g_wb = if wb[1] > 0.0 { wb[1] } else { 1.0 };

    let ow = w / 2;
    let oh = h / 2;
    let mut rgb = vec![0u8; ow * oh * 3];

    for by in 0..oh {
        for bx in 0..ow {
            let mut acc = [0f32; 3];
            let mut cnt = [0f32; 3];
            for dy in 0..2 {
                for dx in 0..2 {
                    let (y, x) = (by * 2 + dy, bx * 2 + dx);
                    let c = cfa.color_at(y, x);
                    if c < 3 {
                        let v = ((mosaic[y * w + x] as f32 - black) / range).clamp(0.0, 1.0);
                        acc[c] += v;
                        cnt[c] += 1.0;
                    }
                }
            }
            let idx = (by * ow + bx) * 3;
            for c in 0..3 {
                let mut val = if cnt[c] > 0.0 { acc[c] / cnt[c] } else { 0.0 };
                val = (val * wb[c] / g_wb).clamp(0.0, 1.0).powf(1.0 / 2.2);
                rgb[idx + c] = (val * 255.0) as u8;
            }
        }
    }
    (rgb, ow, oh)
}

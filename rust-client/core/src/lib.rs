//! The "data" leg of the spike: a vector store that lives in Rust/WASM.
//!
//! Exact brute-force cosine KNN — the same (non-ANN) approach sqlite-vec uses
//! today, and plenty fast for a photo library's worth of vectors. Embeddings are
//! L2-normalized on insert, so a dot product *is* cosine similarity. Vectors are
//! stored flattened in one contiguous `Vec<f32>` for cache-friendly scanning.
//!
//! R2: this replaces a sqlite-vec backend (compiling C-SQLite + the extension to
//! wasm32-unknown-unknown needs a WASI/emscripten libc). The JS interface in
//! `store.ts` is backend-agnostic, so sqlite-vec can slot in behind it later.

use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub struct VecStore {
    dim: usize,
    ids: Vec<u32>,
    /// `ids.len() * dim` normalized components, row-major.
    vecs: Vec<f32>,
}

fn normalize(v: &mut [f32]) {
    let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm > 0.0 {
        for x in v.iter_mut() {
            *x /= norm;
        }
    }
}

#[wasm_bindgen]
impl VecStore {
    #[wasm_bindgen(constructor)]
    pub fn new(dim: usize) -> VecStore {
        VecStore {
            dim,
            ids: Vec::new(),
            vecs: Vec::new(),
        }
    }

    /// Store one embedding under an integer id. Defensively pads/truncates to `dim`.
    pub fn insert(&mut self, id: u32, emb: &[f32]) {
        let mut v = emb.to_vec();
        v.resize(self.dim, 0.0);
        normalize(&mut v);
        self.ids.push(id);
        self.vecs.extend_from_slice(&v);
    }

    /// Ids of the `k` nearest stored vectors to `query`, nearest first.
    pub fn knn(&self, query: &[f32], k: usize) -> Vec<u32> {
        let mut q = query.to_vec();
        q.resize(self.dim, 0.0);
        normalize(&mut q);

        let mut scored: Vec<(f32, u32)> = self
            .ids
            .iter()
            .enumerate()
            .map(|(i, &id)| {
                let row = &self.vecs[i * self.dim..(i + 1) * self.dim];
                let dot: f32 = row.iter().zip(&q).map(|(a, b)| a * b).sum();
                (dot, id)
            })
            .collect();

        // Descending by cosine similarity (higher = nearer).
        scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        scored.into_iter().take(k).map(|(_, id)| id).collect()
    }

    pub fn len(&self) -> usize {
        self.ids.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ids.is_empty()
    }
}

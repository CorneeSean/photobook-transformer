---
type: Build Playbook
title: "Step 31: Wire the real LAION/open_clip aesthetic scorer + CLIP embedder"
description: Plan to replace the raising _UnavailableQualityModel placeholder with a real local ViT-L/14 CLIP embedder + LAION aesthetic head, turning on best-of-scene curation without breaking the offline default or forcing torch on base installs.
tags: [build, quality, aesthetic, clip, laion, open_clip, curation, plan]
timestamp: 2026-07-18
---

# Step 31: Wire the real LAION/open_clip aesthetic scorer + CLIP embedder

**Status: todo — plan only, not yet implemented.** See [progress.md](/build/progress.md).

> **Numbering note.** This closes the "real LAION/open_clip scorer" item deferred at
> [build step 6](/log.md) and resurfaced at step 14 (see progress.md's
> [Notes / blockers](/build/progress.md#notes--blockers)). Step 23 is already the
> DB-backed smoke test and 30 is the highest used, so this is **step 31** — a new
> follow-on playbook, not a renumbering of the deferred item.

## Why

`select` ranks best-of-scene on `aesthetic_score` (`select.py:195`:
`base = aesthetic_score if not None else float("-inf")`). Today that column is
always `NULL`: `_UnavailableQualityModel` (`_flow.py:126`) raises, `quality()`'s
per-photo failure isolation logs it, and every `blur_score`/`aesthetic_score`/
`clip_embedding` lands `NULL`. The run completes on the tested fallback — but with
**every photo in a scene tied at `-inf`**, so within-scene ranking collapses to the
people/lead/rating boosts alone. For the common face-sparse, unrated album that is
a tie, and "best-of-scene" degrades to "first candidate the query returns." The one
real quality signal (`blur_score`, Laplacian) isn't read by `select` at all. So the
placeholder isn't merely "degraded curation" — it leaves the product's headline
feature effectively off. This wires the real predictor so curation actually runs.

Scope confirmed against code:
- **No migration.** `clip_embedding` is already `Vector(768)` (`models.py:137`) —
  exactly OpenAI CLIP ViT-L/14's image-embedding width, which is also what the LAION
  aesthetic head expects. `aesthetic_score`/`blur_score` columns already exist.
- **No architecture change.** `AestheticScorer.score(bytes)->float` and
  `Embedder.embed(bytes)->list[float]` protocols already exist (`quality.py:50-61`),
  `QualityStage` already injects both (`quality.py:91-104`), and `_flow.py` already
  has the injection seam (`_resolve_quality_deps`, `_flow.py:185`). This is a new
  concrete adapter swapped in behind the existing seam.
- **It's local, not an external API.** Once weights are cached the scorer needs no
  network at runtime, so running it does **not** violate the offline-by-default
  posture (step 29). The only reason it stays opt-*out*-able is the heavy install
  (`torch`), not connectivity.

## Decisions (settled with the user before implementing)

1. **Default posture — capability-gated. [CONFIRMED]** Config `quality.scorer`
   defaults to `"clip"`; `_flow.py` tries to build the real model and, if
   `torch`/`open_clip`/weights are absent, logs a loud warning and falls back to the
   placeholder (NULL scores, existing tested fallback). Mirrors the `captioner`
   string precedent (`config.py`) but auto-degrades instead of erroring, so a base
   install still runs. Chosen over strict opt-in because the scorer is local and is
   the core curation signal — the pitch (AGENTS.md) is "curated book," so it should
   be on wherever the capability exists.
2. **Dependency delivery — optional extra. [CONFIRMED]** `torch` + `open_clip` go in
   `[project.optional-dependencies] scoring`, **not** base `dependencies`, so
   `pip install album` stays lean for anyone who only wants the placeholder/offline
   path. The dev/CI env installs the extra (`uv sync --extra scoring`) so basedpyright
   resolves the imports and the opt-in adapter test can run.
3. **Weights provenance / license — pinned (web-verified 2026-07-18).** Both are
   permissive, so no license blocker:
   - **CLIP backbone:** OpenAI CLIP ViT-L/14 via `open_clip` (`model="ViT-L-14"`,
     `pretrained="openai"`) — 768-dim image embedding, matching `Vector(768)`
     (`models.py:137`). License **MIT** (the `open_clip` library and the OpenAI CLIP
     weights/model card both MIT).
   - **Aesthetic head:** `christophschuhmann/improved-aesthetic-predictor`, weights
     file `sac+logos+ava1-l14-linearMSE.pth` — a linear head over the same ViT-L/14
     768-dim features. Repo license **Apache-2.0**. (A sibling `ava+logos-l14-linearMSE.pth`
     exists in the same repo; the `sac+logos+ava1` variant is the general-purpose one
     used for Stable-Diffusion-style aesthetic scoring — use it unless a spot-check
     says otherwise.)
   - Record both URLs + licenses in the `fetch-aesthetic-weights` task docstring.
     Sources: [improved-aesthetic-predictor](https://github.com/christophschuhmann/improved-aesthetic-predictor),
     [open_clip](https://github.com/mlfoundations/open_clip),
     [openai/clip-vit-large-patch14](https://huggingface.co/openai/clip-vit-large-patch14).

## Design

**One local model, both protocols — mirrors the placeholder.** OpenAI CLIP
ViT-L/14's image encoder produces the 768-dim embedding; the LAION head is a small
linear/MLP on top of it. So a single loaded model backs *both* `embed()` (the
vector) and `score()` (embedding → head → scalar), exactly as
`_UnavailableQualityModel` implements both protocols in one class and is injected as
both collaborators (`_resolve_quality_deps`, `_flow.py:185-198`).

**Avoid the double forward pass.** `quality._try_score` (`quality.py:145-156`) calls
`self._aesthetic.score(image)` then `self._embedder.embed(image)` separately. With
one shared instance backing both, cache the last image's CLIP features (one-entry
memo keyed on a hash of the bytes) so `score` then `embed` on the same thumbnail runs
the CLIP forward once. Contained entirely in the adapter — **no stage change**.

**Fail loud on shape drift.** `embed()` must return a length-768 list; assert it so a
wrong model/pretrained pairing surfaces immediately rather than as a pgvector insert
error.

## New adapter — `src/album/analysis/aesthetic.py`

Home it in `analysis/` alongside `tone_estimator.py` / `captioner.py` (the other
heavy/local ML), keeping it **Prefect-free** and out of `flow/` (folder-structure
rule #4). Lazy-import `torch`/`open_clip` **inside** methods (Immich-client pattern)
so the module imports without the extra installed, and so basedpyright/collection
don't hard-require torch.

```python
class ClipAestheticModel:
    """Real LAION-style aesthetic predictor + CLIP embedder (ViT-L/14, 768-dim).
    One model backs both protocols (AestheticScorer + Embedder) — same reasoning
    as _UnavailableQualityModel. Lazily loads torch/open_clip + weights on first
    use; caches the last image's CLIP features so score()+embed() on the same
    thumbnail forward once."""

    def __init__(self, settings: QualitySettings) -> None: ...
    def _features(self, image: bytes) -> "Tensor":   # cached by bytes-hash
        ...
    def embed(self, image: bytes) -> list[float]:    # 768 floats; assert len
        ...
    def score(self, image: bytes) -> float:          # features -> linear head
        ...
```

Device + precision from config (`cpu`/`fp32` default — no GPU in the sandbox; the
builder rejects `fp16` on `cpu`). Drop the unused text tower after load (visual tower
+ aesthetic head are all that's needed) to save ~0.5 GB — see the memory section.
Weights loaded from the config'd cache dir; a missing weights file raises a clear,
actionable error that the `_flow.py` builder turns into the graceful fallback
(decision 1).

## Config — `src/album/config.py` (rule #6: only place that reads env)

Add a `QualitySettings` sub-model (env `QUALITY__*`, nested-delimiter pattern), on
`Settings` as `quality: QualitySettings = QualitySettings()`:

```python
class QualitySettings(BaseModel):
    scorer: str = "clip"            # "clip" | "none"; validated in the builder, not here
    device: str = "cpu"             # "cpu" | "mps" (Apple Silicon) | "cuda"; validated in the builder
    precision: str = "fp32"         # "fp32" | "fp16"; builder rejects fp16 on cpu (MPS/CUDA only)
    clip_model: str = "ViT-L-14"
    clip_pretrained: str = "openai"
    weights_dir: str = ".cache/aesthetic"      # open_clip + LAION head cache
    aesthetic_weights: str = "sac+logos+ava1-l14-linearMSE.pth"
```

Keep the `scorer`-string→model mapping/validation **out** of `config.py` (same
import-cycle reason the `captioner` registry is kept out).

## Wiring — `src/album/flow/_flow.py`

Replace the "fill unset with placeholder" body of `_resolve_quality_deps`
(`_flow.py:185-198`) with a builder that, when a collaborator wasn't injected, tries
the real model and degrades gracefully:

```python
def _build_quality_model(settings) -> AestheticScorer | Embedder:
    """Real ClipAestheticModel when quality.scorer == 'clip' and torch/open_clip/
    weights are available; otherwise the raising placeholder (logged). One instance
    backs both protocols."""
    if settings.quality.scorer == "none":
        return _UnavailableQualityModel()
    try:
        from album.analysis.aesthetic import ClipAestheticModel
        return ClipAestheticModel(settings.quality)
    except Exception:            # ImportError (extra not installed) | missing weights
        _log.warning(
            "real aesthetic scorer unavailable (install the 'scoring' extra + fetch "
            "weights) — curation degrades to NULL scores; see step-31 playbook",
            exc_info=True,
        )
        return _UnavailableQualityModel()
```

`_resolve_quality_deps` builds **one** shared instance and returns it as both
`aesthetic` and `embedder` (as today with the placeholder), so injected test doubles
still override and unit tests are unaffected. Keep `_UnavailableQualityModel` — it's
now the fallback, not the default. Update the module docstring's "Known gap"
note (`_flow.py:76-87`) to describe the wired scorer + graceful fallback.

## Dependencies — `pyproject.toml`

```toml
[project.optional-dependencies]
scoring = ["torch>=...", "open_clip_torch>=..."]   # pin at implementation
```

CPU torch wheel (sandbox has no GPU). Base `dependencies` stay unchanged, so the
default install and `poe check` don't require torch. Dev/CI installs the extra so
basedpyright resolves the (lazily-imported) symbols and the opt-in test can run.

## Weights sourcing / offline — `poe` task (mirror `bake-immich-ml`)

Add `fetch-aesthetic-weights` (parallel to `bake-immich-ml`): a one-time,
internet-touching task that downloads the ViT-L/14 open_clip weights + the LAION head
into `quality.weights_dir`, pinned, with provenance + license recorded in its
docstring — so a real run is fully offline afterward (matching step 24's posture).

## Runtime memory footprint & lifetime

Estimated **incremental** RAM on top of the existing pipeline process. Assumes CPU,
fp32 (CPU can't run CLIP fp16 usefully), batch 1 (the `quality.py:145` per-photo
loop), and one shared instance in one process (current threaded runner — `quality`
is a single task with an internal loop, *not* per-photo fan-out).

| Component | RAM |
|---|---|
| `torch` + `open_clip` imported libs (CPU/MPS build) | ~0.3–0.5 GB |
| CLIP ViT-L/14 weights, full both towers (~428 M × 4 B) | ~1.71 GB |
| — visual tower only (~304 M × 4 B) after dropping the text tower | ~1.22 GB |
| LAION aesthetic head (small MLP) | ~4 MB (negligible) |
| Forward-pass activations (257 tokens, batch 1) | ~50–150 MB transient |

- **Steady-state delta ≈ 2.0–2.2 GB** (full) / **~1.5–1.7 GB** (text tower dropped).
- **Load-time spike ≈ 2.5 GB, brief:** `open_clip` holds the fp16 checkpoint
  (~0.85 GB) *and* the materialized fp32 model (~1.71 GB) before freeing the state
  dict. **Size headroom to this, not the steady figure.**
- **Budget ~2.5 GB headroom** for the process when `scoring` is enabled. Runs
  locally on an 8 GB machine (it's the only heavy *local* model in the default path —
  step 29 dropped Claude; tone/caption defaults are classical/template; Immich's
  CLIP/face models run in their own containers, not this process). This ~2.5 GB is
  the core justification for keeping torch an **opt-in extra** (decision 2): a base
  install pays 0 GB of it.

**Cheap win — drop the unused text tower** after load (`del` the text transformer /
build visual-only): the embedder + aesthetic head only need the visual tower, saving
**~0.5 GB** steady-state. Do this in `ClipAestheticModel.__init__`.

**Apple Silicon (incl. M1 MacBook Air) — supported.** M1 has full PyTorch MPS
(Metal) support (needs macOS 12.3+); set `quality.device="mps"` to run on the GPU via
unified memory — materially faster than CPU, same ~1.7 GB (in the shared pool). CPU
stays the portable default. Tier-dependent:
- **16 GB:** comfortable on MPS.
- **8 GB:** works but tight, especially if the local Immich Docker stack (server + its
  own CLIP/face ML container + Postgres + Redis, ~3–5 GB) runs on the same machine —
  expect SSD swap. Mitigations: **`precision="fp16"` on MPS** (halves weights to
  ~0.85 GB — the big 8 GB win; fp16 is viable on MPS, unlike CPU), the text-tower drop
  (~0.5 GB), and not co-locating the full Immich stack (point at an Immich on another
  host, or run `quality` as its own resumable pass when Immich's ML isn't busy).
- **Fanless throttling:** the Air will thermally throttle under sustained scoring of
  thousands of thumbnails → slower, but functionally fine (idempotent, resumable).

**Model lifetime — whole-run (default) vs stage-scoped (constrained targets).** As
wired, `_resolve_quality_deps` builds the model at `Pipeline` construction, so the
~1.7 GB stays resident for the **whole run**, not just `quality`. That's fine locally
— keep it simple. It's also a *clean cut point*: nothing downstream needs the
in-memory model (`clip_embedding` is written only in `quality.py` and read only via
the pgvector DB query in `similarity.py`; scores go to DB columns), so for a
memory-constrained deployment the model can be **fully released after `quality`**:
lazy-load on `quality`'s first photo (already planned) **and** have `QualityStage`
own the model's lifetime (inject a *builder*, not a long-lived instance the
`Pipeline` also pins) so a post-`_process` drop + `gc.collect()` actually frees it.
Caveat: CPU torch frees the weight tensors to the allocator, but RSS may not fully
return to the OS (arena retention) — the win is that peak doesn't *stack* onto
`render`/`assemble`, not that RSS drops to zero mid-run. **Implement whole-run
residency first; add the stage-scoped release only if a constrained target needs it.**

Disk (not RAM): `fetch-aesthetic-weights` caches ~0.9 GB (CLIP checkpoint + tiny
head); torch install ~0.3–1 GB one-time (platform-dependent) → budget ~1.5–2 GB disk.

## Testing (TDD — write first, per [working-agreement.md](/process/working-agreement.md))

- **Stage unit tests unchanged.** `quality`/`select` tests already inject stub
  scorers/embedders, so they stay pure-logic and torch-free — the point of the
  protocol seam. Add a stage test asserting the shared instance is used for both
  collaborators (score+embed) and that a length≠768 embedding is rejected.
- **New adapter test — opt-in, torch-guarded, out of `poe check`.** New file guarded
  by `pytest.importorskip("torch")` + `pytest.importorskip("open_clip")` and skipped
  unless weights are present (env gate, like step 24's `bake` gate) — so default
  collection/`poe check` neither imports torch nor downloads weights. Give it its own
  `[tool.poe.tasks.test-scoring]` (not in `check`), mirroring `test-immich-ml`.
  Assert: `embed()` returns 768 finite floats; `score()` returns a finite float in
  the expected range on a couple of fixture images (a clearly good vs clearly bad
  frame ⇒ higher score for the good one — a monotonicity sanity check, not a golden
  value); score+embed on the same bytes runs the CLIP forward once (memo hit).
- **Embedding round-trip already covered.** `test_pgvector_integration.py` already
  exercises a real 768-dim vector through the ORM + `select`'s cosine query — a real
  embedding drops straight into it.
- **`_flow.py` builder test.** `quality.scorer == "none"` ⇒ placeholder; extra/weights
  absent ⇒ placeholder + a warning logged (assert the log), not a raise; injected
  double still overrides.

## Docs to update

- [quality stage](/stages/quality.md) + module docstring — aesthetic/embedder now
  real; note the `scoring` extra + weights fetch + graceful fallback.
- [pipeline-execution.md](/foundations/pipeline-execution.md) — the `quality` row
  ("aesthetic + CLIP embedding (ML, deferred)") → no longer deferred.
- [data-model.md](/reference/data-model.md) — `aesthetic_score`/`clip_embedding`
  are now populated on a normal run (given the extra).
- [progress.md](/build/progress.md) — move the step-31 row Outstanding→Completed;
  strike the "Real LAION/open_clip scorer not wired" bullet from Notes / blockers.
- [log.md](/log.md) — record the change once implemented.

## Verification (when implemented)

- `uv run poe check` (ruff, format, basedpyright, xenon, pytest) stays green
  **without** the `scoring` extra pulling torch into the default gate.
- `uv run poe test-scoring` (with the extra + fetched weights) passes.
- Manual, once a live Immich is reachable (currently the standing sandbox gap — see
  progress.md Notes): run `quality` over a real album, confirm non-NULL
  `aesthetic_score`/`clip_embedding`, then confirm `select` picks the visibly better
  frame within a scene (the behavior the placeholder can't deliver). Until then, the
  monotonicity sanity test on fixture images is the offline stand-in.

## Risks / landmines

- **Memory / install size.** ~2.5 GB process headroom + ~1.5–2 GB one-time disk when
  `scoring` is enabled — see [Runtime memory footprint & lifetime](#runtime-memory-footprint--lifetime).
  Keeping torch in an extra (not base deps) is the mitigation; state the size in the
  extra's note so nobody adds it to base deps.
- **basedpyright vs an uninstalled extra.** Lazy imports keep the module importable;
  the dev/CI env must `uv sync --extra scoring` so pyright resolves the symbols — else
  `reportMissingImports`. Do **not** paper over it with blanket `# pyright: ignore`.
- **CPU throughput.** ViT-L/14 on CPU over thousands of thumbnails is slow but
  one-shot per photo and idempotent (`_already_scored`), so a resumed run doesn't
  repay it. Speed is a config flip, not code: `quality.device="mps"` (Apple Silicon)
  or `"cuda"`.
- **Ruff `except` landmine still applies** (progress.md Toolchain landmine): the
  `_build_quality_model` `except` must be a single clause or `# fmt: skip`.

---
type: Proposal
title: Offline analysis — replace the Claude vision call with a classical approach
description: Proposal (not yet planned) to drop the pipeline's last external API dependency by replacing the single Claude vision call with classical, ML-free logic — tone via computer vision, captions via metadata templating — keeping Claude/a local VLM injectable as an optional richer mode.
tags: [proposal, offline, classical, caption, enhance, analysis, next-step, needs-planning]
timestamp: 2026-07-17
---

# Offline analysis — replace the Claude vision call with a classical approach

**Status: proposal — needs proper planning before any implementation.** This is a
summary to anchor a later planning pass (brainstorming →
[writing a plan](/process/implementation-workflow.md)), not a plan itself.

## Motivation

The per-photo Claude vision call (`agents/analysis.py`, via `PhotoAnalyzer.ensure`)
is the pipeline's **last external-API dependency**. Everything else is already
hermetic: offline Immich ML (step 24), local `darktable-cli` (step 22), the
`HtmlPrinter` default (no Lulu creds). Replacing this call makes a full run
possible with **zero external API and zero API key**, fully deterministic and
offline.

Cost is **not** the driver — the full pass is ~$1–$4/album
([AI-pass cost estimate](/build/ai-cost-estimate.md), open Q5). Offline operation,
no key, privacy, and determinism are.

## The idea: split one call into two classical jobs

The call does two unrelated things; each has a good classical replacement:

* **Job A — tone deltas** (`exposure_ev`, `temp_shift_k`, `shadows`,
  `highlights`) → **classical computer vision**: auto-exposure to a target
  luminance, gray-world/white-patch AWB, histogram-percentile shadow/highlight.
  Produces exactly the four bounded scalars the pipeline wants; deterministic;
  fits AGENTS.md rule 5 ("AI estimates parameters, never generates pixels").
  `enhance.py` already uses `skimage.exposure.match_histograms` — half the
  building blocks are present. LLM-guessing EV from a 250px thumbnail was always a
  weak use of an LLM.

* **Job B — grounded caption** → **classical metadata templating**. Most of a
  photo-book caption's value is *who / where / when*, which is **structured data
  already in the DB**, not pixels: `Photo.person_ids`→`Person.name`, geocoded
  `Photo.city/state/country`, the `album.yml` segment label/place, and EXIF
  capture time. A template composer assembles e.g. *"Alex and Sam — Harbour
  walk, Lisbon, May 2026"* with no model. It reuses the helpers that already
  compute this grounding to feed the prompt today (`_resolve_person_names`,
  `_format_place`, `_segment_line`, `_location_line`). Photo-book captions are
  label-like, so this fits the medium.

## What is lost

Only **pixel-derived scene semantics** — "at the water's edge at sunset", "a
plate of grilled fish". That genuinely needs a VLM/Claude. Who/where/when is
covered without one.

## Design posture

Classical as the **default** (fully offline, no `torch`), with Claude — or a local
VLM (Florence-2 / Moondream / Ollama-served) — kept **injectable** as an optional
"rich caption" mode. Mirrors the printer abstraction (`HtmlPrinter` default,
`LuluPrinter` opt-in): the online path stays a quality upgrade behind the existing
injection seam, not the baseline.

## Open questions to settle when planning

* **Caption template + grammar:** name joining ("A and B", "A, B and C"),
  pluralization, place-component dedup (partly in `_format_place`), and
  empty-field fallbacks. Must hold the genericity guard across both album shapes
  (honeymoon vs. `smith-wedding`).
* **Seam:** a `Captioner` protocol next to `AestheticScorer`/`Embedder`, vs.
  composing inside `PhotoAnalyzer`. Keep the `AnalysisResult` shape, or split tone
  and caption into two outputs now that they no longer share a call?
* **Tone estimator:** the exact CV → darktable-XMP mapping and the existing field
  bounds; confirm output stays within `AnalysisResult`'s `Field(ge=, le=)` ranges.
* **Validation:** no API, so validate against real image fixtures + the honeymoon
  / `smith-wedding` album fixtures; assert captions read well for both shapes.

## Scope note

This is the per-photo vision call. The `--source-document` **segment-extraction**
agent ([single-command-ingestion.md](/build/single-command-ingestion.md) step 3 /
build step 28) is a separate Claude text/PDF call — one per *album*, also
localizable but lower priority; out of scope for this proposal.

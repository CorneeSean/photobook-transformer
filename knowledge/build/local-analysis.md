---
type: Proposal
title: Offline analysis — replace the Claude vision call with a classical approach
description: DONE (step 29). Dropped the pipeline's last external API dependency by replacing the single Claude vision call with classical, ML-free logic — tone via computer vision, captions via metadata templating — keeping Claude/a local VLM injectable as an optional richer mode.
tags: [offline, classical, caption, enhance, analysis, done]
timestamp: 2026-07-17
---

# Offline analysis — replace the Claude vision call with a classical approach

**Status: implemented — build step 29 (see [progress.md](/build/progress.md)).** Delivered
as designed below: a `ClassicalToneEstimator` (CV tone) + a `Captioner` seam
(`TemplateCaptioner` offline default, `ClaudeCaptioner` opt-in, selectable per run via
`CAPTIONER`/`--captioner`), composed by `PhotoAnalyzer`; tone is now always classical.
A default `photobook-generator run` needs no external API and no key. Two design points
that shifted during implementation: tone lives behind its own `ToneEstimator` seam
(always classical, no LLM tone path), and a local VLM was deferred (future `Captioner`
registry entry). The rest below is the original proposal, kept as the rationale of record.

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
  Produces exactly the four bounded scalars the pipeline wants; deterministic; fits
  the bounded-delta rule (estimate deltas, never generate pixels). `enhance.py` already
  uses `skimage.exposure.match_histograms`. LLM-guessing EV from a 250px thumbnail was
  always a weak use of an LLM.

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

## How it resolved (as built)

- **Seam:** separate `ToneEstimator` and `Captioner` protocols in `album.analysis`,
  composed by `PhotoAnalyzer` — tone and caption split into two outputs (they no longer
  share a call).
- **Caption grammar** (name joining, place dedup, empty-field fallbacks) holds the
  genericity guard across honeymoon + `smith-wedding`.
- **Validated** against real image + album fixtures (no API); tone stays within the
  existing `Field(ge=, le=)` bounds.

## Scope note

This is the per-photo vision call. The `--source-document` **segment-extraction**
agent ([single-command-ingestion.md](/build/single-command-ingestion.md) step 3 /
build step 28) is a separate Claude text/PDF call — one per *album*, also
localizable but lower priority; out of scope for this proposal.

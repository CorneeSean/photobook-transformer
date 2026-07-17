---
type: Analysis
title: AI-pass cost estimate (open question 5)
description: What a full Claude vision pass costs for a real album (~300–500 selected photos), grounded in the actual agent code and current Claude pricing — the answer to open question 5.
tags: [cost, ai, claude, sonnet, caption, enhance, analysis, open-questions]
timestamp: 2026-07-17
---

# AI-pass cost estimate — open question 5

Answers [open question 5](/build/open-questions.md#5) — *"What does the full AI
pass cost? Estimate the Claude API spend for ~300–500 selected photos before
running it, and confirm it's acceptable."*

**Verdict: acceptable.** A full album's AI pass costs roughly **$1–$4** — a
rounding error against the print/fulfillment cost. No cost control is needed
before the first real run.

## What the AI pass actually is

Grounded in `src/album/agents/analysis.py`, `agents/_model.py`, and
`flow/photo_analyzer.py`:

* **One combined vision call per *selected* photo** — the "one combined vision
  call" from [architecture.md](/foundations/architecture.md). `PhotoAnalyzer.ensure` is
  the single caller; `enhance` and `caption` share its **one** result, so a
  selected photo is analyzed exactly once (idempotent — a re-run costs $0 for
  already-analyzed photos). *Un*selected photos are never sent.
* **Model:** `claude-sonnet-5` (`agents/_model._DEFAULT_MODEL_NAME`).
* **Input per call:** the system prompt (`_SYSTEM_PROMPT`, ~170 tokens) + the
  PydanticAI output-schema tool for `AnalysisResult` (5 fields + descriptions,
  ~200 tokens) + the text context (`"Analyze this photo."` plus optional
  segment/location/people/meta grounding lines, ~20–60 tokens) + **one
  downscaled thumbnail** (`get_thumbnail(..., size="thumbnail")` — Immich's
  small default, ~250 px longest edge).
* **Output per call:** `AnalysisResult` — 4 bounded floats + a short caption.
  ~80–120 tokens.

## Token and cost math

Anthropic bills images at ≈ `width × height / 750` tokens. The small Immich
`thumbnail` (~250 px) is ≈ **60 image tokens**; a `preview`-size image
(~1440 px, *not* what the code sends today) would be ≈ 1,850.

| | Input tok/photo | Output tok/photo |
|---|---|---|
| As coded (small thumbnail) | ~450–650 | ~80–120 |
| If switched to `preview` image | ~2,300 | ~80–120 |

`claude-sonnet-5` pricing (from the `claude-api` skill): **intro $2 / $10 per 1M
input/output through 2026-08-31**, then standard **$3 / $15**. Today (2026-07-17)
the intro rate applies.

**Per-photo (as coded, small thumbnail):** ≈ **$0.0022** intro / **$0.0033**
standard.

| Album size | Intro ($2/$10) | Standard ($3/$15) |
|---|---|---|
| 300 photos | ~$0.66 | ~$0.99 |
| 500 photos | ~$1.10 | ~$1.65 |

**Sensitivity — the dominant variable is image size.** If tone accuracy ever
needs a larger image, a `preview`-size upload raises the worst case to ≈ $0.0056
(intro) / $0.0084 (standard) per photo → **~$2.80 (intro) / ~$4.20 (standard)
for 500 photos**. Still trivial.

## Levers (none needed at this cost)

* **Prompt caching — not worth it.** The system prompt (~170 tokens) is below
  Sonnet's 2,048-token cache minimum, and the image differs every call, so there
  is no reusable prefix.
* **Batch API** would halve the spend (50% off) but adds async complexity for a
  sub-$5 saving — skip it.
* **Model choice** assumes `claude-sonnet-5`. Opus-tier (`claude-opus-4-8`,
  $5/$25) would be ~2–3× — still under ~$10 for a full album.
* **Out of scope for this question:** the `--source-document` segment-extraction
  agent ([single-command-ingestion.md](/build/single-command-ingestion.md) step 3 /
  build step 28) is **one** extra call per *album*, not per photo — negligible.

## Caveat

Estimated from the pinned code and published pricing, not a metered live run (no
`ANTHROPIC_API_KEY` in this environment — same deferral as steps 9/10/12). Record
the real `usage` figures on the first live pass to close this out for good.

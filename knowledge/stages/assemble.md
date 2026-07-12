---
type: Pipeline Stage
title: assemble
description: Populate Jinja2 layouts and render a print-ready PDF with correct trim, bleed, and spine width.
tags: [stage, custom, assemble, jinja2, pdf, weasyprint]
timestamp: 2026-07-12
---

# `assemble` — custom

Populate Jinja2 HTML templates (a few grid layouts: 1-up, 2-up, 3-up) per
segment/scene, render to a print-ready PDF via WeasyPrint or headless Playwright,
with correct trim size, bleed, and computed spine width. Segment names become
section headers when present.

Template dimensions depend on Lulu's spec — **resolve
[open question #4](/open-questions.md) first** (see
[build step 13](/build-order.md)). Test page count and photo placement given
fixture data, **including a segment-less album**.

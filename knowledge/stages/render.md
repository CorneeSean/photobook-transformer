---
type: Pipeline Stage
title: render
description: Batch darktable-cli invocation over the XMP sidecars, writing corrected JPEGs.
tags: [stage, classical, render, darktable]
timestamp: 2026-07-12
---

# `render` — classical

`darktable-cli` batch invocation over the XMP sidecars written by
[`enhance`](./enhance.md), writing corrected JPEGs to
`data/albums/<slug>/rendered/`.

Not meaningfully unit-testable — **manually verified** (see
[build step 11](/build-order.md)): a rendered file exists, opens, and is visibly
corrected versus its original.

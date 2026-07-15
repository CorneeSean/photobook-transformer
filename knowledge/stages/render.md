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

The orchestration (fetch original, invoke the renderer, idempotency, per-photo
failure isolation, staged-original lifecycle) is unit-tested with an injected
`Renderer`/`OriginalSource` on `tests/test_render.py`. The **real**
`DarktableRenderer` against the real binary is covered by
`tests/test_render_integration.py` (TestContainers playbook step 22): it runs
the real renderer **inside the shipped app Docker image** over a real source
JPEG + a real [`write_xmp_sidecar`](./enhance.md) sidecar and asserts the output
JPEG exists, decodes, and is **visibly brighter** than the source. This is the
automated form of [build step 11](/build-order.md)'s deferred *"a rendered file
exists, opens, and is visibly corrected versus its original"* manual check.

> **Sidecar-format landmine (found by step 22):** darktable applies edits only
> from its real binary *history stack*, never from plain-text attributes. An
> earlier `enhance` schema wrote flat `darktable:exposure="0.5"` attributes,
> which `darktable-cli` **silently ignored** — every render was a no-op copy.
> `enhance` now emits a real history stack (see [enhance.md](./enhance.md)); the
> integration test's "visibly brighter" assertion is what guards against a
> regression to a no-op sidecar.

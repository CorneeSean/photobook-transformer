---
type: Pipeline Stage
title: fulfill
description: Deliver the assembled book via a pluggable Printer — a browsable webpage by default, a physical Lulu order opt-in.
tags: [stage, fulfill, printer, html, lulu, print-api]
timestamp: 2026-07-12
---

# `fulfill` — deliver the finished book via a `Printer`

The final stage turns the laid-out book from [`assemble`](./assemble.md) into a
real deliverable. It owns **no delivery policy of its own**: it delegates to an
injected [`Printer`](../../src/album/printers/base.py) and records the
printer-agnostic reference the printer returns on `Album.output_ref` (idempotent —
a second run is a no-op unless `force=True`). The output *medium* is swappable:

- **`HtmlPrinter` (default).** Renders a single self-contained `index.html` — a
  browsable, page-by-page viewer with captions, segment dividers, and a bit of
  live-flow feel (animated page turns, a progress bar) — from the on-disk
  rendered photos. Needs **no external service or credentials**, so it's the
  default output: unlike every other pipeline dependency (Immich, Claude,
  darktable, Lulu), the HTML book renders from what's already on disk. Images are
  referenced by a path relative to the written file, so the browser loads them
  straight from disk. Styling uses a CDN CSS lib + a little vanilla JS, but it's
  deliberately *one static file*, not a web app.
- **`LuluPrinter` (opt-in).** POSTs the assembled master PDF to Lulu's Print API
  (photo books as a native product type) for a physical copy on the doormat.
  Injected by the caller when wanted, because a real order needs live Lulu
  credentials **and** a PDF hosted at a URL Lulu's servers can fetch — see the
  `UnreachablePdfError` gap. Test against recorded fixtures; **manually verify**
  with a single-copy test order before the real one.

Each printer validates its own precondition (a webpage needs the placed photos; a
Lulu order needs the master PDF), which is what keeps `fulfill` medium-agnostic.
`printers/base.py`'s `iter_book_pages` is the shared reader that reconstructs the
book's pages from the DB the same way `assemble` paginated them, so every
medium-rendering printer lays the book out consistently.

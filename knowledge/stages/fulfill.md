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

- **`HtmlPrinter` (default).** A single self-contained `index.html` — a browsable,
  page-by-page viewer (captions, segment dividers, animated page turns, an "Auto"
  advance) rendered from the on-disk photos, referenced by relative path. Needs **no
  external service or credentials**, so it's the default; deliberately *one static
  file* (CDN CSS + a little vanilla JS), not a web app.
- **`LuluPrinter` (opt-in).** POSTs the assembled master PDF to Lulu's Print API for a
  physical copy. Injected by the caller, because a real order needs live Lulu
  credentials **and** a PDF hosted at a URL Lulu can fetch (see the `UnreachablePdfError`
  gap). Tested against recorded fixtures; **manually verify** with a single-copy test
  order first.

Each printer validates its own precondition (a webpage needs the placed photos; a
Lulu order needs the master PDF), which is what keeps `fulfill` medium-agnostic.
`printers/base.py`'s `iter_book_pages` is the shared reader that reconstructs the
book's pages from the DB the same way `assemble` paginated them, so every
medium-rendering printer lays the book out consistently.

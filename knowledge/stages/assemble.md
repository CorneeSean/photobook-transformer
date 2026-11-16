---
type: Pipeline Stage
title: assemble
description: Lay selected photos into the book's page order (page_number + section pages) and compute the Lulu spine width. Renders no file.
tags: [stage, custom, assemble, layout, page-number, spine]
timestamp: 2026-07-12
---

# `assemble` — custom

Lay out selected photos into the book's page order — a few grid capacities (1-up,
2-up, 3-up) cycled per segment/scene — stamping each placed photo's `page_number`
and inserting a section-header page as each segment's first page (segment names
become the header; a segment-less album skips them). It also computes the Lulu
spine width from the page count for the cover.

**It renders no file.** Turning the layout into a concrete medium is a
[`Printer`](./fulfill.md)'s job, done on demand: the default `HtmlPrinter` renders
a webpage, and the print-ready **master PDF is rendered inside `LuluPrinter`**
(Jinja2 + WeasyPrint, correct trim/bleed) only when a physical order is actually
requested — not unconditionally on every run. The shared reader
`printers/base.py:iter_book_pages` reconstructs the pages from `page_number`, so
every printer lays the book out the same way `assemble` paginated it.

Spine/page dimensions depend on Lulu's spec —
[open question #4](../build/open-questions.md) (see
[build step 13](../build/build-order.md)). Test page count and photo placement given
fixture data, **including a segment-less album**.

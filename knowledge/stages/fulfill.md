---
type: Pipeline Stage
title: fulfill
description: POST the finished PDF to Lulu's Print API — raw photo dump to book on the doormat.
tags: [stage, fulfill, lulu, print-api]
timestamp: 2026-07-12
---

# `fulfill` — Lulu Print API

POST the finished PDF from [`assemble`](./assemble.md) to Lulu's Print API (free,
REST/JSON, webhooks for order status, photo books as a native product type).
End-to-end: raw photo dump → book on the doormat, no manual upload step.

Test against recorded fixtures; **manually verify** with a single-copy test order
before the real one (see [build step 15](/build-order.md)).

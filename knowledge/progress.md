---
type: Progress Tracker
title: Build progress
description: Living per-step status for the build order — updated by the implementing agent as each step moves through the workflow.
tags: [progress, status, tracking, build-order]
timestamp: 2026-07-12
---

# Build progress

Living status of the [build order](/build-order.md), maintained by the
implementing agent as part of the [implementation workflow](/implementation-workflow.md).
Update this file whenever a step changes state. Steps are delivered strictly in
order, one at a time.

**Status values:** `todo` → `in-progress` → `agent-review` → `awaiting-user` →
`done`. Use `blocked` (with a note) if a step cannot proceed.

| # | Step | Branch | Status | Commit | Notes |
|---|------|--------|--------|--------|-------|
| 0 | Scaffold | step-00-scaffold (merged, deleted) | done | 11d290f | `config.py`/`definition.py`/`flow.py` deliberately not created — deferred to steps 3 and 14 |
| 1 | Containers | step-01-containers (merged, deleted) | done | 240fbe0 | vendored Immich's stack as its own file, pulled in via Compose `include:` |
| 2 | Data model + migrations | step-02-data-model (merged, deleted) | done | 68ed96f | |
| 3 | Album definition loading | step-03-album-definition (merged, deleted) | done | 84260ef | |
| 4 | Immich client | step-04-05-immich-client-sync (merged, deleted) | done | 8172dfa | delivered with step 5 (one branch/review) per user; resolves Open Q1 (no CLIP embeddings); live fixture recording skipped — see log |
| 5 | `sync` stage | step-04-05-immich-client-sync (merged, deleted) | done | 8172dfa | delivered with step 4; live "run for real" skipped (no reachable Immich stack) per user |
| 6 | `quality` stage | step-06-quality (merged, deleted) | done | a6c4b9e | real OpenCV blur; aesthetic scorer + CLIP embedder injected (protocols) — real LAION/open_clip impls deferred (heavy) |
| 7 | `cluster` stage | — | todo | — | |
| 8 | `select` stage (MMR) + `db/similarity.py` | — | todo | — | |
| 9 | Agent (`analysis.py`) | — | todo | — | |
| 10 | `enhance` stage | — | todo | — | |
| 11 | `render` stage | — | todo | — | manual verification |
| 12 | `caption` stage | — | todo | — | |
| 13 | `assemble` stage | — | todo | — | resolves Open Q4 |
| 14 | Wire the flow | — | todo | — | |
| 15 | `fulfill` stage | — | todo | — | |
| 16 | Prove genericity | — | todo | — | |

## Notes / blockers

- **Process change (from step 4 onward, per user):** deliver each step with the
  full workflow *except* the human-review gate — after `uv run poe check` is green
  and the sub-agent review is clean, squash and fast-forward-merge autonomously
  rather than waiting for user approval.
- **Toolchain landmine:** `ruff` 0.15.21's formatter, under `target-version =
  "py314"`, rewrites `except (A, B):` into invalid paren-less syntax (no fixed
  release yet). Avoid parenthesized multi-type `except` clauses — use separate
  clauses or `# fmt: skip`. See [log](/log.md) 2026-07-13.
- **Deferred live checks:** steps 4–5 fixtures/sync were built without a reachable
  Immich stack; re-verify against a live instance + the honeymoon album on the
  first real sync (see [Immich API notes](/immich-api.md)).

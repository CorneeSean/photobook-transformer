---
type: Feature Idea
title: "User-directed curation + an editable album document (scores, count ranges, regenerate)"
description: A forward-looking, not-yet-planned feature note. Put the user in the loop over curation — per-segment photo-count ranges, a scores/decisions artifact emitted alongside the book, and a `regenerate` command that rebuilds an album from a user-supplied ordered photo list (no re-ingest). The linchpin is a common, serializable "album document" the pipeline emits, the user/UI/DSL edits, and `regenerate` consumes. Written so a follow-up agent can draw the plan.
tags: [idea, curation, select, assemble, output, album-document, regenerate, ui, dsl, unplanned]
timestamp: 2026-07-18
---

# User-directed curation + an editable album document

**Status: idea, not planned.** This is a grounded feature *brief*, not a step
playbook. It records what the user wants, anchors it to the current code, names the
central design decision (a common album-output data structure), and lists the open
questions — so a follow-up agent can turn it into step plans (the way
[folder-structure-ingestion.md](/build/folder-structure-ingestion.md) did for that
feature). **Do not treat any shape below as decided.**

## The want, in one line

Today the pipeline is a black box: `select` silently keeps the best-of-every-scene and
`assemble` lays them out in a fixed order, and the user sees only the finished book.
The user wants to be **in the loop** — to see *why* each photo was kept or dropped and
with what score, to constrain *how many* photos a segment gets, and to **hand back an
edited selection/order** and have the book rebuilt from it without re-ingesting.

## Where the current pipeline stands (grounding)

| Capability wanted | Today | Gap |
|---|---|---|
| Per-segment photo **count range** (e.g. 20–40) | `SelectStage` (`flow/stages/select.py`) picks **all** non-duplicate, non-near-duplicate best-of-scene photos — **no count target anywhere**. Count is emergent from scene count + the `threshold=0.9` near-dup guard. | No notion of a budget/quota per segment or per album. |
| See the **score** of each selected photo | The score exists: `SelectStage._effective_score` = `aesthetic_score` + `lead_boost`/`group_boost`/`rating_boost` (`select.py:190`). | It's computed in-memory and thrown away — never persisted or surfaced. |
| See **why** a photo was discarded | Discards are **silent**: an Immich `duplicate_group_id` repeat, a CLIP near-dup over `threshold`, or an unclustered `scene_id is None` photo simply isn't selected. | No decision record ("kept", "discarded: near-duplicate of #123", "discarded: unrated & lower score"). |
| An **artifact alongside the album** listing the above | The book output lives at `data/albums/<slug>/book/index.html` (`HtmlPrinter`) / `assembled.pdf` (`assemble`). Nothing reports the *reasoning*. | No scores/decisions report emitted next to the book. |
| **Binding** user-chosen order + selection | `assemble` orders by segment position → scene → timestamp (`assemble._group_photos`), stamps `Photo.page_number`, and `iter_book_pages` (`printers/base.py`) reconstructs pages from it. Order is derived, never user-supplied. | No way to say "these photos, in this order" and have it honored. |
| **Regenerate** from a new list (no ingest) | The photos are already in the DB (`Photo.immich_asset_id`/`filename`); `enhance`/`render`/`caption`/`assemble` are per-photo **idempotent** (AGENTS rule 7). `select` is idempotent too (resets `selected` up front). | No entry point that skips `sync`/`select`, applies a user selection+order, restyles only what's needed, and reassembles. |

The single most useful observation: **the "score" the user wants to see is already
computed and immediately discarded, and the discard reasons are known at the point of
decision but never recorded.** Surfacing them is mostly a persistence/reporting problem,
not a new-algorithm problem.

## The four capabilities

### 1. Per-segment count range

A segment could carry a target range (e.g. `min: 20, max: 40`). `select` would then move
from "keep every best-of-scene" to "**fill a budget**": rank a segment's scenes' photos
by `_effective_score`, honor the dup/near-dup guards, and stop at the range — distributing
the budget across the segment's scenes so coverage stays diverse rather than draining one
scene. Must degrade cleanly: **no range set ⇒ today's behavior exactly** (genericity /
segments-optional guard). Natural authoring homes to weigh: the new ingest manifest's
`SegmentSpec` (see [folder-structure-ingestion.md](/build/folder-structure-ingestion.md)),
`album.yml`'s `SegmentYAML`, or the album document below. Open: what "40 for the segment"
means when a segment has many scenes vs one, and whether a **whole-album** budget is also
wanted.

### 2. A scores + decisions artifact

After `select`, emit an ordered, human-readable artifact next to the book (e.g. under
`data/albums/<slug>/`): per segment, the **selected** photos in order with their
`_effective_score` (and its components — aesthetic, people, lead, rating), **and** the
**discarded** ones with a reason ("near-duplicate of *X*", "Immich duplicate", "below
segment budget", "unclustered"). This requires `select` (and possibly `quality`) to
**record its decisions** rather than only flip `Photo.selected`. Consider a `SelectDecision`
row/JSON keyed by photo, or folding it into the album document (§5). The artifact is both a
user-facing report and the raw material the UI/DSL edits.

### 3. `regenerate` — rebuild from a user-supplied ordered list

A new CLI verb (sketch: `photobook-generator regenerate --album <slug> <selection-file>`)
that, **without any ingest**:

- Takes a user-provided ordered list of photos that must **already be in the album**
  (reference by `immich_asset_id` or `filename`; error on anything not in `Photo` for that
  `album_id`).
- Treats the given **order as binding** — bypassing `select`'s ranking and `assemble`'s
  segment→scene→timestamp sort. This is the sharpest conflict with today's derived ordering
  and needs an explicit decision on how binding order coexists with segments/scenes/pages.
- **Restyles only as needed**: photos already `rendered` are reused; a newly-included photo
  runs `enhance`/`render` (per-photo idempotent) before `assemble`. `caption` similarly.
- Re-runs `assemble`/`fulfill` to produce the new book.

Idempotency and `album_id`-scoping must hold (AGENTS rules 3, 7). This is essentially
"apply an edited album document (§5) and re-render", so §3 and §5 are two ends of one
mechanism.

### 4. Future — a layout DSL and a drag-and-drop UI

Longer horizon, explicitly out of scope for the first pass but shaping the data structure:

- **Layout DSL** — declarative rules richer than a flat list: "group these photos on one
  page", "this photo full-bleed", "keep these together", per-page grouping. The first pass
  auto-generates the album document; the DSL is how a human overrides it in text.
- **Simple UI** — drag-and-drop reordering, grouping photos onto a page, seeing scores and
  "discarded as duplicate" inline. The UI reads and writes the **same** album document, so
  it's a view over the data structure, not a parallel path.

Both are reasons the output structure must be **serializable, explicit, and round-trippable**
— not just the current implicit `Photo.page_number` + `iter_book_pages` reconstruction.

## The linchpin — a common "album document"

Everything above converges on one decision: **agree on a serializable data structure for a
laid-out album** that the pipeline *emits*, a human/UI/DSL *edits*, and `regenerate`
*consumes*. Candidate responsibilities:

- Ordered structure: segments → pages → photos (or a flatter ordered list the layout engine
  pages), with per-photo group/layout hints for the future DSL.
- Per-photo **provenance for review**: the `_effective_score` and its components, the
  keep/discard decision and reason, and the source ref (`immich_asset_id`/`filename`).
- A stable **round-trip contract**: emitted by a first pass, editable by hand/UI/DSL, and
  fed back to `regenerate` to reproduce (or amend) the book deterministically.

Design tensions a planner must resolve (do **not** pre-decide here):

- **Relationship to `album.yml`.** `album.yml` is the album's *input definition* (the single
  source of truth for what the album *is* — slug/title/segments/leads; `definition.py`). The
  album document is *output + editable curation state* (what got picked, scored, ordered).
  These are different concerns — keep them separate but linked, or risk muddying the
  "`album.yml` is the one definition" invariant (build step 16).
- **Relationship to the DB.** Selection/order/pagination live on `Photo` columns
  (`selected`, `page_number`, `scene_id`, `segment_id`) and are reconstructed by
  `iter_book_pages` into `BookPage`/`BookPhoto` (`printers/base.py`) — the closest existing
  "output structure". Is the album document a *projection* of those columns (DB stays
  authoritative) or the *source of truth* that `regenerate` writes back into them? This is
  the core question.
- **Format & location.** YAML/JSON under `data/albums/<slug>/`? Reuse the `BookPage`/
  `BookPhoto` shapes as its serialization, or a new schema? Human-diffable vs UI-friendly.
- **Genericity.** The document must not encode occasion concepts — only structural
  album/segment/page/photo/score, matching the [genericity rule](/reference/folder-structure.md).

## Constraints any plan must honor

- **Genericity** — no trip/day/occasion in `src/` or the document (AGENTS rule 1).
- **Segments optional** — every new behavior needs a tested zero-segment / no-range /
  no-document fallback that reproduces today's output (AGENTS rule 2).
- **`album_id`-scoped + idempotent** — `regenerate` and any new stage stay per-album and
  re-runnable (AGENTS rules 3, 7).
- **`album.yml` stays the single input definition** (build step 16) — the album document is a
  distinct artifact, not a second definition.
- **Reuse the existing seams** — `_effective_score`, the `Printer`/`iter_book_pages`
  boundary, and the per-photo idempotent `enhance`/`render` are the leverage points; prefer
  extending them over new parallel machinery.

## Open questions for the planning agent

1. What exactly does a per-segment count range mean across multiple scenes, and is a
   whole-album budget also wanted? Where is it authored (manifest `SegmentSpec` /
   `album.yml` / album document)?
2. Is the album document a **projection** of the `Photo`/DB state or the **source of truth**
   `regenerate` writes back? (Decides everything else.)
3. How does a user's **binding order** coexist with segments, scenes, and pagination — does
   it replace `assemble`'s ordering wholesale, or reorder within segments?
4. Where do `select`'s decisions get **persisted** (new table/column vs the document), and
   how much does `quality` contribute (e.g. "discarded as blurry")?
5. Format/location/schema of the document, and whether it reuses `BookPage`/`BookPhoto`.
6. Sequencing: ship the **scores/decisions artifact** first (pure reporting, low risk),
   then count ranges, then `regenerate`, then UI/DSL? (Suggested, not decided.)

## Suggested reading before planning

`flow/stages/select.py` (`_effective_score`, the silent discards), `db/models.py`
(`Photo.selected`/`page_number`/`scene_id`/`segment_id`/`aesthetic_score`/
`duplicate_group_id`), `flow/stages/assemble.py` (ordering + pagination),
`printers/base.py` (`iter_book_pages` → `BookPage`/`BookPhoto`, the current implicit output
shape), `definition.py` (`album.yml` as the input definition), and
[folder-structure-ingestion.md](/build/folder-structure-ingestion.md) (the manifest
`SegmentSpec`, a candidate home for count ranges).

---
type: Playbook
title: Build order
description: The ordered, commit-per-step plan for building the pipeline, tests before implementation.
tags: [playbook, build-order, tdd, steps]
timestamp: 2026-07-12
---

# Build order

Each step is delivered on its own branch following the
[implementation workflow](/process/implementation-workflow.md) — TDD, a sub-agent review,
then user review, then squash-merge to `main` (never commit to `main` directly) —
and its status is tracked in the [progress tracker](/build/progress.md). Each step ends
with `uv run poe check` green (ruff clean → basedpyright clean → pytest green)
plus those reviews before it lands; steps with no automated test end with a
written-down manual verification instead. Tests come *before* implementation
within each step. See the [Working agreement](/process/working-agreement.md) for the full
commit gate.

**0. Scaffold.** `git init`. `uv init`, Python 3.14. Configure ruff,
`basedpyright`, and pytest in `pyproject.toml`; define the `poethepoet` tasks
(`[tool.poe.tasks]`) and the console entry point (`[project.scripts]`) — scaffolded
as `album`, later renamed to `photobook-generator` (single-command playbook, step 27).
Create the [folder structure](/reference/folder-structure.md). Write a first-pass
**`README.md`** (human-readable: what the project is, prerequisites, how to
initialize it, how to bring the local stack up, and the `uv run poe` workflow
commands) and **`AGENTS.md`** (agent orientation — pointing at this knowledge
bundle as the source of truth). Add a **`.pre-commit-config.yaml`** with
`no-commit-to-branch` (protect `main`), ruff, and basedpyright hooks, and run
`uv run pre-commit install`. *Verify:* `uv run poe check` runs successfully (ruff
+ basedpyright + pytest; zero tests collected is fine), and a test commit made
directly on `main` is rejected by the hook. **Commit.**

**1. Containers.** `Dockerfile` (multi-stage; runtime includes `darktable-cli`)
and `docker-compose.yml` — app + **our own Postgres (with the `pgvector`
extension, e.g. the `pgvector/pgvector` image)** + Immich's stack + Prefect. Keep
our Postgres separate from Immich's; our migrations must never touch their schema.
*Manually verify:* `docker compose up` brings all services healthy; Immich's web
UI is reachable; `docker compose run app darktable-cli --version` works;
`CREATE EXTENSION vector;` succeeds on our database. **Commit** with the
verification in the body.

**2. Data model + migrations.** [`db/models.py`](/reference/data-model.md) (Album, Segment,
Photo) and the first alembic migration — which must `CREATE EXTENSION IF NOT
EXISTS vector` before creating the embedding column. *Test first:* in-memory
SQLite fixture; an album with segments and an album *without* any can both be
created and related. **Commit.**

**3. Album definition loading.** `config.py` + `definition.py`
([`album.yml`](/reference/album-definition.md) → `Album` + `Segment` rows), normalizing all
segment times to **UTC** on load (explicit offset wins; otherwise apply
`Album.timezone`). *Test first:* a trip-shaped YAML, a wedding-shaped YAML, and a
segment-less YAML all load correctly; a time with an offset and a time relying on
the album `timezone` both land as the same UTC instant; a time with neither an
offset nor an album `timezone` raises; overlapping or malformed segments raise
clearly. **Commit.**

**4. Immich client.** *Test first*, against recorded HTTP fixtures: asset
listing, EXIF parsing, duplicate groups, thumbnail fetch. **Resolve
[open question #1](/build/open-questions.md) here** — does Immich expose CLIP
embeddings? Record the answer. **Commit.**

**5. [`sync`](/stages/sync.md) stage.** Immich → DB, scoped to `album_id`,
normalizing each photo's capture time to **UTC** (use the EXIF offset if Immich
exposes one; otherwise assume `Album.timezone`, and flag photos where the zone is
ambiguous). *Test first:* idempotency (running twice doesn't duplicate rows);
correct `album_id` tagging; a naive-EXIF photo and an offset-bearing one both land
as correct UTC. Then run for real against the honeymoon album. **Commit.**

**6. [`quality`](/stages/quality.md) stage.** Blur + aesthetic scoring. *Test
first:* the known-blurry fixture scores below the known-sharp one; scores land in
the DB. **Commit.**

**7. [`cluster`](/stages/cluster.md) stage.** *Test first, thoroughly* — pure
logic, and the place genericity lives or dies:
- photos assign to the right segments by timestamp
- **an album with no segments still produces scenes** (temporal fallback)
- **an album with no GPS still produces scenes** (temporal fallback)
- an album *with* GPS takes the spatial path
- edge cases: photo outside every segment (flagged, not dropped), photo with a
  NULL timestamp (handled explicitly)

**Commit.**

**8. [`select`](/stages/select.md) stage (MMR) + `db/similarity.py`.** Put the
vector search behind a substitutable interface, so MMR logic is testable without
Postgres. *Test first, thoroughly:* highest-scoring photo per scene wins;
near-duplicates skipped; Immich-flagged duplicates skipped; a scene of
all-similar photos still yields exactly one selection — all with similarity
stubbed, on in-memory SQLite. *Separately*, test the real `pgvector` query
against a Postgres testcontainer. **Commit.**

**9. Agent.** `agents/analysis.py` — the single combined agent whose output model
holds bounded tone deltas *and* a caption. *Test first* with `TestModel`: output
validates; out-of-bounds delta values rejected by Pydantic; **the agent handles a
photo with no segment context** (produces a generic caption without erroring).
Only then wire in real Claude calls, and sanity-check on ~20 photos. **Commit.**

**10. [`enhance`](/stages/enhance.md) stage.** Drives the shared analysis call
(idempotent; persists tone deltas + caption), then reconciles the cross-camera
baseline (histogram match) with the per-photo AI deltas → XMP sidecars. *Test
first:* the reconciliation math; XMP parses as valid XML with expected tags; the
analysis call is not repeated for a photo that already has stored deltas.
**Commit.**

**11. [`render`](/stages/render.md) stage.** `darktable-cli` batch invocation.
*Manually verify:* a rendered file exists, opens, and is visibly corrected versus
its original. **Commit** with that noted.

**12. [`caption`](/stages/caption.md) stage.** Reads the caption from the shared
analysis call (triggering it only if `enhance` hasn't already), grounded by
whatever context the album has. *Test first:* a photo with stored analysis is
captioned without a second API call; a segment-less album still gets a caption.
*Manually verify:* spot-check ~20 captions for accuracy and non-genericity.
**Commit.**

**13. [`assemble`](/stages/assemble.md) stage.** Jinja2 → print-ready PDF; segment
names become section headers when present. *Test first:* page count and photo
placement given fixture data, **including a segment-less album**. *Manually
verify:* the PDF opens with correct trim/bleed. **Resolve
[open question #4](/build/open-questions.md) (Lulu's spec) first** — it drives template
dimensions. **Commit.**

**14. Wire the flow.** `flow.py` + `cli.py`. *Manually verify:* `photobook-generator
run --album honeymoon-2026` runs end-to-end; the Prefect UI shows it. Now that
Postgres handles concurrent writes, tasks can write per-photo and keep true
failure isolation — but **watch connection-pool sizing** under Prefect's fan-out
(cap task concurrency, or the pool will exhaust). Update `README.md`'s
run-locally section with the real end-to-end command. **Commit.**

**15. [`fulfill`](/stages/fulfill.md) stage.** Lulu API submission. *Test first*
against recorded fixtures. *Manually verify* with a single-copy test order before
the real one. **Commit.**

**16. Prove genericity — the real test of the abstraction.** Run a *structurally
different* album end-to-end: a wedding (hour-scale segments, single day, GPS
present) or a segment-less dump. Adding it must require **only** an `album.yml`
and an Immich album. If it needs *any* code change, that's a bug in the
abstraction — fix it, don't work around it. **Commit.**

---

Steps **17+** (adopting Immich API surface that didn't exist at steps 4–5 —
faces/people, lead-face prioritization, rating/OCR/geocoding) are broken out into
their own playbook: [Immich enrichment](/build/enrichment.md). Steps **21+**
(TestContainers integration testing) are broken out into their own playbook:
[TestContainers](/build/testcontainers.md).

---

**25. Parallelize the flow (fan-out groups).** Apply the DAG from
[`pipeline-execution.md`](/foundations/pipeline-execution.md): sequential stages keep the
shared session, grouped stages open their own pooled session; two `submit()`/`result()`
fences (group 1 join at `select`, group 2 at `assemble`), commit-before-fanout +
`expire_all()`-after-join. *Test first:* wiring asserts the partial order + fences +
failure-abort; a Postgres-testcontainer test exercises real concurrent per-task sessions.
Only `flow/_flow.py` changes. **Caught in review:** `enhance` and `caption` share
`PhotoAnalyzer.ensure`'s idempotency columns, so `caption` is joined behind `enhance`'s
future (not just `render`), closing a double-vision-call race. **Commit.**

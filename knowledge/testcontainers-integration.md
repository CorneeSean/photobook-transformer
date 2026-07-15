---
type: Playbook
title: TestContainers integration testing — build order (steps 21+)
description: Promote the highest-value external boundaries from mocked unit tests to real containerized integration tests (pgvector, darktable-cli), without depending on any internet-hosted model. Immich ML is an explicitly-deferred optional follow-on.
tags: [playbook, build-order, testing, testcontainers, pgvector, darktable, integration]
timestamp: 2026-07-15
---

# TestContainers integration testing — build order (steps 21+)

A follow-on to the [build order](/build-order.md) and the
[Immich enrichment playbook](/immich-enrichment.md). The pipeline's fast test
suite is in-memory SQLite + injected doubles (`respx` for Immich/Lulu HTTP,
PydanticAI `TestModel` for the agent, a numpy `StubSimilarity` for pgvector). One
real container is already used — `tests/test_similarity.py` runs the pgvector
query against a Postgres [testcontainer](/working-agreement.md). This playbook
promotes the **highest-value** boundaries to real containerized integration
tests, **without depending on any internet-hosted model**.

## What each external dependency allows

| Boundary | Container feasible? | Verdict |
|---|---|---|
| **Postgres + pgvector** | Yes — already used in `test_similarity.py` | **In scope** (21a) — biggest win |
| **darktable-cli** (`render`) | Yes — a binary in our app Docker image, no network model | **In scope** (21b) — closes a deferred manual check |
| **Prefect server** | Yes, but `prefect_test_harness` already covers wiring | **Optional** (21c) — low marginal value |
| **Claude / Anthropic** (`agent`/`enhance`/`caption`) | No — real API + key + internet model | **Omit** — no workaround; `TestModel` stays |
| **Immich** (`sync` faces, lead resolution, `quality` embeddings) | Partial — server containerizes, but face/CLIP jobs **download ML models from the internet** | **Deferred optional** (21d) — see below |
| **Lulu Print API** (`fulfill`) | No — external paid API, no sandbox creds | **Omit** — `respx` fixtures stay |
| **LAION/open_clip quality scorer** | No — downloads pretrained weights | **Omit** — also still unwired (step 6 deferral) |

The rule that shapes scope: **no test may depend on a model fetched from the
internet** (Anthropic, Immich's CLIP/face models, LAION weights). Everything left
in scope runs from a pinned container image or a binary we already ship.

## The steps

Each follows the [implementation workflow](/implementation-workflow.md) (TDD where
applicable, `uv run poe check` green — or the container run as the manual
verification, sub-agent review, squash-merge; human-review gate stays waived per
the step-4-onward note in [progress](/progress.md)). Mark the new tests so the
fast SQLite suite stays the default and containers run in a separate gate
(`@pytest.mark.integration`, or a `poe` task that opts in). The Ryuk reaper now
works, so plain `uv run poe check` is green — `TESTCONTAINERS_RYUK_DISABLED` is
**no longer needed** (the old sandbox landmine was resolved 2026-07-15; see
[progress](/progress.md) notes).

**21. pgvector schema + integration harness** *(in scope — highest value)*.
A session-scoped `pgvector` testcontainer fixture that runs **`alembic upgrade
head`** (not `Base.metadata.create_all`), yielding a Postgres-backed `Session`.
Then:
- Run **`select` end-to-end with the real `PgVectorSimilarity`** (not the numpy
  `StubSimilarity`) over real embeddings — today the MMR *logic* and the pgvector
  *query* are tested separately (`test_select.py` vs `test_similarity.py`); this
  joins them, exercising the near-dup guard against a real cosine query.
- Exercise `cluster`'s spatial/temporal path and the `person_ids` JSON column,
  `UniqueConstraint`s, and FK/NULL semantics that SQLite only approximates.
- Folds the **manual migration round-trip + autogenerate-drift check** done by
  hand each schema step (17, 17b) into automated CI.
*Test:* migrations apply to a real Postgres; `select` picks match the stub-based
expectations against the real query; a re-run is idempotent. **Commit.**

**22. darktable render integration** *(in scope — **done**, `4399ab3`)*. Runs the
real `DarktableRenderer` **inside the shipped app Docker image** (which contains
`darktable-cli`) over a real source JPEG + a real `write_xmp_sidecar` sidecar
(`tests/test_render_integration.py`, `@pytest.mark.integration`); asserts the
output JPEG exists, decodes, and is **visibly brighter** than the source. Closes
build step 11's deferred *"visibly corrected versus its original"* manual check,
no internet model.
- **Found + fixed a real bug:** `enhance`'s XMP sidecars used a made-up *flat*
  `darktable:exposure="…"` attribute schema that `darktable-cli` **silently
  ignores** — every render was a no-op, so no tone correction ever reached the
  image. Fixed by rewriting `write_xmp_sidecar` to emit darktable's real binary
  *history stack* (exposure / shadhi / temperature modules; struct layouts pinned
  to the app image's darktable 5.0.1). The "visibly brighter" assertion is the
  regression guard.
- **Landmine:** shells out to the `docker` CLI, not docker-py — this sandbox's
  `~/.docker` credential helper (`docker-credential-gcloud`) crashes under Python
  3.14 (`No module named 'imp'`), breaking docker-py's image build; the CLI is
  fine. **Commit.**

**23. DB-backed flow smoke test** *(optional)*. Run the whole Prefect flow against
the real Postgres container with **Immich/Claude/Lulu still injected as doubles**,
asserting cross-stage data flow and real per-stage commits — automating the
step-14 live verification minus the unreachable boundaries. Lower marginal value
than 21/22 (the flow's wiring is already unit-tested via `prefect_test_harness`);
take it only if the real-DB cross-stage coverage proves worth the runtime.

## Explicitly deferred (optional, later)

**24. Immich ML integration** *(deferred optional — needs an internet model, or a
pre-baked cache)*. An Immich server **can** be containerized, and its **HTTP
contract** (upload an asset, album pagination, `get_faces` returning `[]` before
detection) could be tested against a real instance. But **face detection and CLIP
embeddings require downloading ML models from the internet**, so live
faces/lead-resolution/embedding behavior stays deferred unless we **pre-bake a
model-cache image** (heavy: Immich's `model-cache` volume baked into a fixture
image). Until then:
- keep the `respx`-fixture contract tests for `ImmichClient` (steps 4–5/17/17b);
- leave live-Immich (faces appearing after the async detection job, real
  lead-reference upload + detection timing) as the standing **manual** deferral
  already tracked in [progress](/progress.md)'s Notes / blockers.
Revisit if/when a pre-baked Immich+model-cache image is worth maintaining, or a
real instance becomes reachable in the dev environment.

## Out of scope (no container substitute)

- **Anthropic / Lulu / LAION** — no container replaces a hosted API/model;
  `TestModel` (agent/enhance/caption), `respx` fixtures (Lulu), and the
  `_UnavailableQualityModel` placeholder stay as-is.

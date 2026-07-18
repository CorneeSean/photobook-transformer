---
type: Playbook
title: TestContainers integration testing — build order (steps 21+)
description: Promote the highest-value external boundaries from mocked unit tests to real containerized integration tests (pgvector, darktable-cli), without depending on any internet-hosted model. Immich ML is an explicitly-deferred optional follow-on.
tags: [playbook, build-order, testing, testcontainers, pgvector, darktable, integration]
timestamp: 2026-07-15
---

# TestContainers integration testing — build order (steps 21+)

A follow-on to the [build order](/build/build-order.md) and the
[Immich enrichment playbook](/build/enrichment.md). The pipeline's fast test
suite is in-memory SQLite + injected doubles (`respx` for Immich/Lulu HTTP,
PydanticAI `TestModel` for the agent, a numpy `StubSimilarity` for pgvector). One
real container is already used — `tests/test_similarity.py` runs the pgvector
query against a Postgres [testcontainer](/process/working-agreement.md). This playbook
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

Each follows the [implementation workflow](/process/implementation-workflow.md) (TDD where
applicable, `uv run poe check` green — or the container run as the manual
verification, sub-agent review, squash-merge; human-review gate stays waived per
the step-4-onward note in [progress](/build/progress.md)). Mark the new tests so the
fast SQLite suite stays the default and containers run in a separate gate
(`@pytest.mark.integration`, or a `poe` task that opts in). The Ryuk reaper now
works, so plain `uv run poe check` is green — `TESTCONTAINERS_RYUK_DISABLED` is
**no longer needed** (the old sandbox landmine was resolved 2026-07-15; see
[progress](/build/progress.md) notes).

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

**22. darktable render integration** *(in scope — **done**, `8aac81b`)*. Runs the
real `DarktableRenderer` **inside the shipped app Docker image** (which contains
`darktable-cli`) over a real source JPEG + a real `write_xmp_sidecar` sidecar
(`tests/test_render_integration.py`, `@pytest.mark.integration`); asserts the
output JPEG exists, decodes, and is **visibly brighter** than the source. Closes
build step 11's deferred *"visibly corrected versus its original"* manual check,
no internet model. Validated the darktable XMP history-stack format — see
[enhance.md](/stages/enhance.md) History note.
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

## Immich ML integration — done via a pre-baked model image

**24. Immich ML integration** *(**done** — pre-baked offline model image)*. The
deferral was "face detection and CLIP embeddings download ML models from the
internet, so a hermetic test needs a pre-baked model-cache image." We now build
exactly that, **locally** — and after a one-time setup the test fetches **no ML
model** (ever) and **no images** from the internet.

- **The bake** ([`ml-baked.Dockerfile`](../../tests/fixtures/immich/ml-baked.Dockerfile)):
  `FROM immich-machine-learning:<pinned>` + a build-time `snapshot_download` that
  **mirrors `ModelBase._download`** (same repo, on-disk layout, CPU `.onnx` variants
  only — ~0.77 GB of models; ~3.1 GB image, base-ML-dominated). Runs with
  `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` so a cache miss fails loudly instead of
  reaching the network.
- **One-time setup, then offline.** `poe bake-immich-ml` bakes that image **and**
  pre-pulls the server/db/redis images, so it is the *only* internet-touching step;
  `compose up` at test time then finds every image present and pulls nothing.
- **The stack** ([`docker-compose.integration.yml`](../../tests/fixtures/immich/docker-compose.integration.yml)):
  server + the baked ML image + the (digest-pinned) vectorchord Postgres + valkey,
  pinned to a concrete `IMMICH_VERSION`, ephemeral, on an ephemeral host port, with
  reverse-geocoding disabled (a second startup internet hit — out of scope). It is
  driven from Python via testcontainers' `DockerCompose` (Ryuk-reaped teardown +
  port resolution — no hand-rolled subprocess/cleanup), the same testcontainers
  dependency the pgvector tests already use.
- **The test**
  ([`test_immich_ml_integration.py`](../../tests/album/immich/test_immich_ml_integration.py)):
  bootstraps an admin + API key over Immich's auth API, uploads
  `skimage.data.astronaut()` (a public-domain NASA face — no personal photo
  committed) through the real `ImmichClient`, waits out the async job cascade, and
  asserts (a) `get_faces` returns a real machine-learning detection, (b) the real
  `LeadFaceResolver` `reference_image` path uploads + polls real async detection to
  a resolved face (closes the step-17b timing deferral), and (c) smart search
  returns the asset (CLIP embedded offline). **Opt-in** (`IMMICH_ML_INTEGRATION=1`
  / `poe test-immich-ml`) so the ~3 GB image + multi-service stack stays out of the
  default `poe check` gate.

**Still out of scope** (no container substitute or not worth the second bake):
- keep the `respx`-fixture contract tests for `ImmichClient` (steps 4–5/17/17b) —
  they stay the fast default; this is the live counterpart, not a replacement;
- **reverse-geocoding** (`city/state/country`, step 20) — disabled here; would
  need a second geodata bake;
- **recognition of a *named* person** — Immich clusters detected faces into
  unnamed people; naming is a UI action, so the test asserts real detection, not a
  named match.

## Out of scope (no container substitute)

- **Anthropic / Lulu / LAION** — no container replaces a hosted API/model;
  `TestModel` (agent/enhance/caption), `respx` fixtures (Lulu), and the
  `_UnavailableQualityModel` placeholder stay as-is.

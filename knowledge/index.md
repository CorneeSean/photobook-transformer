---
okf_version: "0.1"
---

# Photo Album Pipeline — Knowledge Bundle

An OKF knowledge bundle describing the photo-album pipeline: turning a raw dump of
photos into a curated, enhanced, captioned, print-ready photo book with minimal
manual work. This bundle is the project's source of truth.

## Foundations

* [Overview](/foundations/overview.md) - What the pipeline does and the ground rules for building it.
* [Domain model](/foundations/domain-model.md) - The `Album` + `Segment` abstraction that keeps the pipeline generic.
* [Architecture](/foundations/architecture.md) - Batch pipeline over Immich + Postgres, Prefect orchestration, PydanticAI, with rationale.
* [Pipeline execution shape](/foundations/pipeline-execution.md) - The stage DAG: linear as run today, two parallelisable fan-out groups, and the Prefect change to unlock them (`.html` companion diagram alongside).
* [Working agreement](/process/working-agreement.md) - TDD, static typing, the git commit gate, and local workflow commands.
* [Quality practices](/process/quality-practices.md) - How code is shaped: classes with injected dependencies, encapsulation, and class-based tests with scoped fixtures.

## Reference

* [Data model](/reference/data-model.md) - The Album, Segment, and Photo SQLAlchemy ORM models and their invariants.
* [Album definition](/reference/album-definition.md) - The single `album.yml` file that defines an album.
* [Tech stack](/reference/tech-stack.md) - The chosen tool for each concern.
* [Folder structure](/reference/folder-structure.md) - Target repository layout and structural rules.

## Execution

* [Implementation workflow](/process/implementation-workflow.md) - The per-step handoff loop: branch, TDD, sub-agent review, user review, squash, merge.
* [Build order](/build/build-order.md) - The ordered, one-step-at-a-time plan.
* [Immich enrichment](/build/enrichment.md) - Follow-on steps (17+) adopting the faces/people, rating, OCR, and geocoding API surface Immich has grown since steps 4–5.
* [Immich dependency decision](/build/immich-dependency-decision.md) - Keep-or-replace analysis for the Immich stack: what we actually consume, the open-source substitutes (InsightFace, pgvector dedup), and why replaceability is preserved rather than acted on.
* [TestContainers integration](/build/testcontainers.md) - Follow-on steps (21+) promoting the highest-value boundaries (pgvector, darktable-cli) to real containerized integration tests; Immich ML deferred optional.
* [Progress tracker](/build/progress.md) - Living per-step status.
* [Open questions](/build/open-questions.md) - Unresolved questions to settle early.
* [Pipeline stages](/stages/) - One concept per stage, from `ingest` to `fulfill`.

## Log

* [Change log](/log.md) - Chronological history of this bundle.

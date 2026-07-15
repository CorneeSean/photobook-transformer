---
okf_version: "0.1"
---

# Photo Album Pipeline — Knowledge Bundle

An OKF knowledge bundle describing the photo-album pipeline: turning a raw dump of
photos into a curated, enhanced, captioned, print-ready photo book with minimal
manual work. This bundle is the project's source of truth.

## Foundations

* [Overview](/overview.md) - What the pipeline does and the ground rules for building it.
* [Domain model](/domain-model.md) - The `Album` + `Segment` abstraction that keeps the pipeline generic.
* [Architecture](/architecture.md) - Batch pipeline over Immich + Postgres, Prefect orchestration, PydanticAI, with rationale.
* [Working agreement](/working-agreement.md) - TDD, static typing, the git commit gate, and local workflow commands.
* [Quality practices](/quality-practices.md) - How code is shaped: classes with injected dependencies, encapsulation, and class-based tests with scoped fixtures.

## Reference

* [Data model](/data-model.md) - The Album, Segment, and Photo SQLAlchemy ORM models and their invariants.
* [Album definition](/album-definition.md) - The single `album.yml` file that defines an album.
* [Tech stack](/tech-stack.md) - The chosen tool for each concern.
* [Folder structure](/folder-structure.md) - Target repository layout and structural rules.

## Execution

* [Implementation workflow](/implementation-workflow.md) - The per-step handoff loop: branch, TDD, sub-agent review, user review, squash, merge.
* [Build order](/build-order.md) - The ordered, one-step-at-a-time plan.
* [Immich enrichment](/immich-enrichment.md) - Follow-on steps (17+) adopting the faces/people, rating, OCR, and geocoding API surface Immich has grown since steps 4–5.
* [TestContainers integration](/testcontainers-integration.md) - Follow-on steps (21+) promoting the highest-value boundaries (pgvector, darktable-cli) to real containerized integration tests; Immich ML deferred optional.
* [Progress tracker](/progress.md) - Living per-step status.
* [Open questions](/open-questions.md) - Unresolved questions to settle early.
* [Pipeline stages](/stages/) - One concept per stage, from `ingest` to `fulfill`.

## Log

* [Change log](/log.md) - Chronological history of this bundle.

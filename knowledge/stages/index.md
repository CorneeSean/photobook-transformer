# Pipeline stages

The pipeline runs these stages in order. Stages are referenced by **name**, not
number — numbering drifts as stages get added or split. See
[Build order](/build/build-order.md) for the step-by-step build plan and
[Architecture](/foundations/architecture.md) for how they are orchestrated.

## Immich (not our code)

* [ingest](/stages/ingest.md) - Import an album's photos into a dedicated Immich album (manual, once per album).
* [dedupe](/stages/dedupe.md) - Immich's built-in duplicate detection; we consume the results.
* faces/people - Immich's built-in face detection + people recognition; `sync` consumes it (no local face model). See [Immich enrichment](/build/enrichment.md).

## Our pipeline

* [sync](/stages/sync.md) - The boundary — pull Immich asset list, EXIF, duplicate groups, and faces/people into our DB.
* lead_faces - Resolve `album.yml`'s optional priority subjects (the couple, the baby) to Immich person IDs and mark `Person.is_lead`. Runs after `sync`; no-op when unconfigured. See [Immich enrichment](/build/enrichment.md).
* [quality](/stages/quality.md) - Score blur and aesthetics; compute embeddings if Immich doesn't expose them.
* [cluster](/stages/cluster.md) - Assign photos to segments and detect scenes; degrades gracefully.
* [select](/stages/select.md) - Diversity-aware, people/lead-aware best-of-scene via MMR (guarantees leads appear).
* [enhance](/stages/enhance.md) - Estimate bounded tone deltas (classical/offline by default); classical code applies them via XMP.
* [render](/stages/render.md) - Batch darktable-cli over the XMP sidecars.
* [caption](/stages/caption.md) - Grounded captions from the shared analysis call.
* [assemble](/stages/assemble.md) - Jinja2 layouts → print-ready PDF.
* [fulfill](/stages/fulfill.md) - Deliver via a pluggable Printer — HTML book (default) or Lulu order.

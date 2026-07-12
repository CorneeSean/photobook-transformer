# Pipeline stages

The pipeline runs these stages in order. Stages are referenced by **name**, not
number — numbering drifts as stages get added or split. See
[Build order](/build-order.md) for the step-by-step build plan and
[Architecture](/architecture.md) for how they are orchestrated.

## Immich (not our code)

* [ingest](/stages/ingest.md) - Import an album's photos into a dedicated Immich album (manual, once per album).
* [dedupe](/stages/dedupe.md) - Immich's built-in duplicate detection; we consume the results.

## Our pipeline

* [sync](/stages/sync.md) - The boundary — pull Immich asset list, EXIF, and duplicate groups into our DB.
* [quality](/stages/quality.md) - Score blur and aesthetics; compute embeddings if Immich doesn't expose them.
* [cluster](/stages/cluster.md) - Assign photos to segments and detect scenes; degrades gracefully.
* [select](/stages/select.md) - Diversity-aware best-of-scene via MMR.
* [enhance](/stages/enhance.md) - AI estimates bounded tone parameters; classical code applies them via XMP.
* [render](/stages/render.md) - Batch darktable-cli over the XMP sidecars.
* [caption](/stages/caption.md) - Grounded captions from the shared analysis call.
* [assemble](/stages/assemble.md) - Jinja2 layouts → print-ready PDF.
* [fulfill](/stages/fulfill.md) - POST the finished PDF to Lulu's Print API.

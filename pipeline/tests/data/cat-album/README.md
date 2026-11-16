# `cat-album` — sample album data

A ready-made sample album: **30 cat photos** (from [The Cat API](https://thecatapi.com),
downscaled) organized into **5 time/place categories** — used to render a real
book through the pipeline's back half, offline, with no Immich/Claude/darktable/Lulu.

**Attribution:** the photos come from [The Cat API](https://thecatapi.com). They are
included only as downscaled test fixtures, stay the property of their owners, and are
not covered by the repository's licence notice.

- `photos/NN.jpg` — the 30 images (stand in for `render`'s output).
- `manifest.json` — the album title, the 5 `Segment`s (label + place + UTC
  time window), and per-photo `timestamp` / `caption` / `lat` / `lon`. Each
  photo's `segment_hint` is for humans only: the real
  [`cluster`](../../../src/album/flow/stages/cluster.py) stage re-derives the segment
  from the photo's timestamp.

The 5 segments (a cat's day, `2026-06-21`):

| # | Category | Place | Window |
|---|----------|-------|--------|
| 1 | Dawn Patrol | The Windowsill | 06:00–09:00 |
| 2 | Garden Expedition | The Back Garden | 09:00–12:00 |
| 3 | Café Society | Café Miaou | 12:00–15:00 |
| 4 | Rooftop Siesta | The Rooftops | 15:00–18:00 |
| 5 | Night Prowl | The Alley | 18:00–22:00 |

## Rebuild the book

```sh
uv run python scripts/build_cat_album.py
```

Runs the real `cluster` + `assemble` stages and the default `HtmlPrinter`, then
writes the browsable book to `book/index.html` (gitignored — regenerable). Open
that file in a browser and click **“Open the book →”**.

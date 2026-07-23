"""Emit a static HTML preview gallery to eyeball an album's curation from the DB.

Reads every ``Photo`` for an album out of Postgres, resolves each to a real image
on disk (the external originals tree, falling back to ``rendered_path``), and
writes a single self-contained HTML file that references those files by absolute
``file://`` path — so opening it locally shows the actual photos. Selected photos
get a green tick, unselected a red cross and a dimming shadow that lifts on hover.

Toolbar features: sort (filename / aesthetic / selected-first / capture time),
hide/show the unselected set, a CLIP near-neighbour focus + side-by-side compare
mode, and collapsible date sections (group by hour / day / week / month / year, or
a custom "every N units" interval; timestamps shown in the album's timezone). This
is the curation surface we'll ultimately export a JSON payload from to drive
render + print.

Run: ``uv run python scripts/preview_gallery.py [album-slug] [-o OUT.html]``
Defaults to the only album with photos; writes next to this script's data dir.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.config import get_settings
from album.db.models import Album, Photo
from album.db.session import new_session

REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_path(photo: Photo, originals_root: Path | None) -> str | None:
    """Absolute on-disk path for a photo's image, or None if nothing exists."""
    candidates: list[Path] = []
    if originals_root is not None:
        candidates.append(originals_root / photo.filename)
    if photo.rendered_path:
        candidates.append((REPO_ROOT / photo.rendered_path).resolve())
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _thumb_data_uri(path: str, max_px: int = 640) -> tuple[str, float] | None:
    """A small, EXIF-oriented JPEG thumbnail as ``(data_uri, aspect)``, or None on failure.

    The source photos are ~24 MP; decoding one to paint a grid cell — and re-rasterizing
    it on hover — stalls the browser. The grid uses these ~0.4 MP thumbnails instead; the
    lightbox still points at the full-res original. ``aspect`` is the EXIF-rotated
    width/height; the grid sets each card's ``aspect-ratio`` from it so card widths are
    correct for the justified (uniform-height) layout *before* the lazy image decodes.
    """
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)  # bake in rotation so it matches the original
            im.thumbnail((max_px, max_px))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            width, height = im.size
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
    except Exception:  # best-effort thumbnail; any decode/encode failure → fall back to src
        return None
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return uri, round(width / height, 4) if height else 1.0


def _normalize(vec: list[float] | None) -> list[float] | None:
    """L2-normalize an embedding so cosine similarity is a plain dot product.

    Rounded to 4 dp to keep the emitted JSON small; precision is far beyond what
    a 0.3-distance filter needs.
    """
    if not vec:
        return None
    norm = math.sqrt(sum(component * component for component in vec))
    if norm == 0:
        return None
    return [round(component / norm, 4) for component in vec]


def _find_album(session: Session, album_slug: str | None) -> Album:
    if album_slug:
        album = session.scalar(select(Album).where(Album.slug == album_slug))
    else:
        # default: the album that actually has photos
        album = session.scalars(
            select(Album).join(Photo).group_by(Album.id).order_by(Album.id)
        ).first()
    if album is None:
        raise SystemExit(f"No album found (slug={album_slug!r}).")
    return album


def _tzinfo(name: str | None) -> ZoneInfo | timezone:
    """The album's IANA timezone (album.yml), or UTC if unset/unknown."""
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            print(f"note: unknown timezone {name!r}; falling back to UTC.")
    return UTC


def _localize(ts: datetime | None, tz: ZoneInfo | timezone) -> datetime | None:
    """A UTC (or naive-assumed-UTC) DB timestamp expressed in the album's tz."""
    if ts is None:
        return None
    aware = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    return aware.astimezone(tz)


def _build_items(
    photos: Sequence[Photo], originals_root: Path | None, tz: ZoneInfo | timezone
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    missing = 0
    for photo in photos:
        path = _resolve_path(photo, originals_root)
        if path is None:
            missing += 1
            continue
        # Localise the capture time so the date buckets + displayed time reflect
        # when the shot was taken, not the stored UTC instant. All None when the
        # photo has no timestamp (→ a trailing "Undated" section when grouping).
        local = _localize(photo.timestamp, tz)
        thumb = _thumb_data_uri(path)  # (data URI, aspect) — used by the grid
        thumb_uri, aspect = thumb if thumb else (None, 1.0)
        items.append(
            {
                "filename": photo.filename,
                "src": Path(path).as_uri(),  # full-res original, used only by the lightbox
                "thumb": thumb_uri,
                "aspect": aspect,  # width/height → the card's aspect-ratio in the grid
                "selected": bool(photo.selected),
                "aesthetic": photo.aesthetic_score,
                "emb": _normalize(photo.clip_embedding),
                "ts": local.timestamp() if local else None,  # true instant, for time sort
                # civil (wall-clock) epoch: album-local time encoded as if UTC, so the
                # browser can bucket + label at any interval in album-local time using
                # UTC accessors, regardless of the viewer's own timezone.
                "lts": local.replace(tzinfo=UTC).timestamp() if local else None,
                "day": local.strftime("%Y-%m-%d") if local else None,
                "time": local.strftime("%H:%M") if local else None,
            }
        )
    if missing:
        print(f"note: {missing} photo(s) had no image on disk; skipped.")
    return items


def _collect(album_slug: str | None) -> tuple[dict[str, str], list[dict[str, object]]]:
    originals_root_str = get_settings().immich.external_host_root
    originals_root = Path(originals_root_str) if originals_root_str else None

    with new_session() as session:
        album = _find_album(session, album_slug)
        tz = _tzinfo(album.timezone)
        photos = session.scalars(
            select(Photo)
            .where(Photo.album_id == album.id)
            .order_by(Photo.timestamp, Photo.filename)
        ).all()
        # detach a plain snapshot of the album fields we need before the session closes
        snapshot = {"slug": album.slug, "title": album.title, "tz": album.timezone or "UTC"}
        return snapshot, _build_items(photos, originals_root, tz)


def render_html(album: dict[str, str], items: list[dict[str, object]]) -> str:
    # `</` is escaped so a filename containing "</script>" can't close the inline
    # <script> and break the page; filenames are still escaped again in the JS
    # captions (esc()) before landing in innerHTML.
    data_json = json.dumps(items).replace("</", "<\\/")
    total = len(items)
    selected = sum(1 for it in items if it["selected"])
    # Styles + front-end logic live in sibling .css/.js loaded by absolute file:// URL
    # (a plain <link>/classic <script> — works locally without a server, same as the
    # image paths). Regenerated HTML always points at the committed files, so all three
    # stay in lockstep.
    css_src = (REPO_ROOT / "scripts" / "preview_gallery.css").as_uri()
    js_src = (REPO_ROOT / "scripts" / "preview_gallery.js").as_uri()
    return _TEMPLATE.format(
        title=html.escape(album["title"]),
        slug=html.escape(album["slug"]),
        tz=html.escape(album["tz"]),
        total=total,
        selected=selected,
        data_json=data_json,
        css_src=html.escape(css_src, quote=True),
        js_src=html.escape(js_src, quote=True),
    )


_TEMPLATE = """<!doctype html>
<html lang="en">
<!--
  Photo preview gallery — generated by scripts/preview_gallery.py from the
  pipeline's Postgres DB. Open locally (file://) to eyeball an album's curated set;
  images are referenced by absolute path on disk. Regenerate with:
      uv run python scripts/preview_gallery.py [album-slug]

  FEATURES
  - Grid of every photo. Selected = green ✓ badge, full colour; unselected = red ✕
    badge, very slightly dimmed + grayscaled, lifting on hover.
  - Staging: click a photo's badge to toggle its selection (selected→unselect,
    unselected→select). It's a browser-only *suggestion* until exported + saved to the
    DB, so a staged badge is softened (lower opacity + halo) and its card gets a dashed
    accent ring. The header shows a live "N staged" tally; "Show changed" filters to just
    the staged photos. (The whole UI reflects the staged/effective selection; only the
    changed-vs-DB delta is tracked separately.)
  - Sort: filename, aesthetic ↓/↑, selected-first, capture time ↑/↓.
  - "Hide/Show unselected" toggle (live count in the header).
  - Group by date: "none" (flat grid), the presets hour / day / week / month / year,
    or "custom…" (an "every N minutes/hours/days/weeks/months/years" interval). Each
    bucket is a collapsible <details> section headed by its date/range, photo count and
    selected count, with Expand-all / Collapse-all. Buckets are computed in the album's
    timezone (fixed spans anchored at the epoch, weeks aligned to Monday; months/years by
    calendar); photos with no timestamp fall into a trailing "Undated" section. Grouping
    is suspended while focus mode is active (focus is an inherently flat ranked view).
  - Lightbox gallery: click any photo → centred modal, full image at original aspect
    ratio (fit to viewport). ← / → (or on-screen ‹ ›) page prev/next in display order
    without closing; Esc / backdrop / ✕ closes. Caption shows selection, filename,
    ★ aesthetic, position (i / n). The sel/unsel pill is a staging toggle here too
    (or press "s") — same browser-only suggestion as clicking a grid badge.
  - Focus mode (labelled "similar" in the UI): each photo has a top-right "similar"
    checkbox. Checking one filters the grid to photos within a cosine distance of it
    (embeddings are L2-normalised, so similarity is a dot product computed in the browser
    — no NxN matrix), sorted nearest-first. Each card reveals ★ aesthetic · d(istance) ·
    s(imilarity). Checking another photo's box swaps the anchor; "Exit similar" (or
    unchecking) leaves. A photo with no CLIP embedding shows a greyed "n/a" marker.
    Entering/switching focus scrolls the focused photo to the top; exiting scrolls back
    to that same photo in the restored grid (a lightweight view-history; falls back to
    the saved scroll position if a filter now hides it).
  - "≤ dist" slider (0–0.30, 0.01 steps) tunes the focus cutoff; entering focus resets it
    to a tight 0.10 default. Re-render is debounced 300 ms; a hover tooltip previews the
    value at the cursor before clicking.
  - Compare: in similar mode there's no single-shot lightbox — clicking any photo opens
    the side-by-side compare (anchor left, clicked photo right; clicking the anchor itself
    compares it against its closest neighbour). The headline metric is similarity, with
    cosine distance shown secondary and a neighbour counter (e.g. "2 / 10") stacked under
    them in the centre column, tracking which neighbour you're on. ← / → cycle the
    right-hand photo through the similar set (the left
    stays the anchor). Either photo's pill stages a selection toggle; "◎ Focus this" (or
    press "f") promotes the compared photo to the anchor and re-compares it against its
    nearest neighbour — walking the similarity graph. "⇄ Swap & focus" does the same but
    first flips BOTH photos' selection (the "the right shot wins — swap my pick and keep
    going" move). Non-focused photos still expose a "compare" checkbox as an alternate way
    in. Outside similar mode, clicking a photo opens the usual single lightbox.
  - Section order: a toolbar toggle flips the date sections between oldest-first and
    newest-first (the "Undated" bucket always trails); within-section order still follows
    the Sort control.
  - URL sync: sort, group (+ custom interval + order), hide-unselected, focus (by
    filename), dist, and compare are mirrored into the query string (history.replaceState)
    and restored on load — views are shareable / reload-stable.

  IMPLEMENTATION NOTES
  - Per-album photo data (incl. normalised embeddings + localised capture day/month/time)
    is embedded as JSON in an inline <script>; the styles and front-end logic live in the
    sibling scripts/preview_gallery.css and scripts/preview_gallery.js, loaded by a plain
    <link> / classic <script src> at absolute file:// URLs (works locally with no server,
    like the image paths). Nothing hits the network.
  - The grid shows small EXIF-oriented JPEG thumbnails embedded as data: URIs (the
    source photos are ~24 MP — decoding/scaling those in a 277px cell stalls the
    browser). The lightbox alone loads the full-res original (one at a time).
  - Cards are built ONCE by cloning the <template id="cardTpl"> (no HTML-string concat)
    and kept persistent in the DOM with JS references. Flat (ungrouped) filtering/sorting
    only toggles CSS classes (.hidden, .focused, .grid.focusing) and reorders nodes via
    the CSS `order` property — no node churn, no image reloads. Grouping reparents those
    same persistent card nodes into per-date section grids (an infrequent, user-driven
    action), so a finished pop-in animation is not replayed.
  - Filenames + album title/slug are HTML-escaped (esc() in JS, html.escape in Python)
    and `</` in the JSON payload is neutralised, so odd filenames can't break the page.
-->
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preview · {title}</title>
<link rel="stylesheet" href="{css_src}">
</head>
<body>
<header>
  <h1>{title} <small>{slug} · {tz}</small></h1>
  <div class="stat"><b id="shown">{total}</b> shown · <b id="selCount">{selected}</b> selected / {total} total<span id="chgStat" hidden> · <b id="chgCount">0</b> staged</span></div>
  <span class="spacer"></span>
  <label class="ctl">Sort
    <select id="sort">
      <option value="filename">filename</option>
      <option value="aes-desc">aesthetic ↓</option>
      <option value="aes-asc">aesthetic ↑</option>
      <option value="sel-first">selected first</option>
      <option value="time-asc">time ↑</option>
      <option value="time-desc">time ↓</option>
    </select>
  </label>
  <label class="ctl">Group
    <select id="group">
      <option value="none">none</option>
      <option value="hour">hour</option>
      <option value="day">day</option>
      <option value="week">week</option>
      <option value="month">month</option>
      <option value="year">year</option>
      <option value="custom">custom…</option>
    </select>
  </label>
  <label class="ctl" id="customCtl" hidden>every
    <input type="number" id="gn" min="1" value="1">
    <select id="gunit">
      <option value="minute">minutes</option>
      <option value="hour">hours</option>
      <option value="day">days</option>
      <option value="week">weeks</option>
      <option value="month">months</option>
      <option value="year">years</option>
    </select>
  </label>
  <button id="groupOrder" hidden>↑ Oldest first</button>
  <button id="expandAll" hidden>Expand all</button>
  <button id="collapseAll" hidden>Collapse all</button>
  <button id="toggleUnsel">Hide unselected</button>
  <button id="showChanged">Show changed</button>
  <label class="ctl distctl" id="distCtl" hidden>≤ dist
    <input type="range" id="distSlider" min="0" max="0.3" step="0.01" value="0.1">
    <b id="distVal">0.10</b>
  </label>
  <button id="exitFocus" hidden>✕ Exit similar</button>
</header>
<template id="cardTpl">
  <div class="card">
    <div class="badge"></div>
    <div class="tools">
      <label class="compare" title="Compare with focused photo"><input type="checkbox">compare</label>
      <label class="focus" title="Show photos similar to this"><input type="checkbox">similar</label>
    </div>
    <img loading="lazy" alt="">
    <div class="meta"><span class="fn"></span><span class="aes"></span></div>
    <div class="simchip"><span class="fn"></span><span class="sim"><b class="saes"></b><span class="sv"></span></span></div>
  </div>
</template>
<main>
  <div class="grid" id="grid"></div>
  <div id="sections" hidden></div>
  <div class="empty" id="empty" hidden>Nothing to show.</div>
</main>
<div class="lightbox" id="lightbox">
  <button class="close" id="lbClose" title="Close (Esc)">✕</button>
  <button class="nav prev" id="lbPrev" title="Previous (←)">‹</button>
  <button class="nav next" id="lbNext" title="Next (→)">›</button>
  <img id="lbImg" src="" alt="">
  <div class="lbcap" id="lbCap"></div>
  <div class="cmp" id="lbCmp" hidden>
    <figure><img id="cmpA" src="" alt=""><figcaption id="cmpAcap"></figcaption></figure>
    <div class="cmpmid" id="cmpMid"></div>
    <figure>
      <img id="cmpB" src="" alt=""><figcaption id="cmpBcap"></figcaption>
      <div class="cmpactions">
        <button class="cmpfocus swap" id="cmpSwapBtn" title="Flip both photos' selection (staged), then focus the right one and compare its nearest neighbour">⇄ Swap &amp; focus</button>
        <button class="cmpfocus" id="cmpFocusBtn" title="Make this the focus anchor and compare its nearest neighbour">◎ Focus this →</button>
      </div>
    </figure>
  </div>
</div>
<script>const DATA = {data_json};</script>
<script src="{js_src}"></script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", nargs="?", help="album slug (default: first with photos)")
    parser.add_argument("-o", "--out", type=Path, help="output HTML path")
    args = parser.parse_args()

    album, items = _collect(args.slug)
    out = args.out or (REPO_ROOT / "data" / "albums" / album["slug"] / "preview-gallery.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(album, items), encoding="utf-8")
    print(f"Wrote {len(items)} photos → {out}")
    print(f"Open: file://{out}")


if __name__ == "__main__":
    main()

"""Emit a static HTML gallery to eyeball the current selected set from the DB.

Reads every ``Photo`` for an album out of Postgres, resolves each to a real image
on disk (the external originals tree, falling back to ``rendered_path``), and
writes a single self-contained HTML file that references those files by absolute
``file://`` path — so opening it locally shows the actual photos. Selected photos
get a green tick, unselected a red cross and a dimming shadow that lifts on hover.
Sort by aesthetic score and hide/show the unselected set from the toolbar.

Run: ``uv run python scripts/selection_gallery.py [album-slug] [-o OUT.html]``
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
from pathlib import Path

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


def _thumb_data_uri(path: str, max_px: int = 640) -> str | None:
    """A small, EXIF-oriented JPEG thumbnail as a ``data:`` URI, or None on failure.

    The source photos are ~24 MP; decoding one to paint a 277px grid cell — and
    re-rasterizing it on hover — stalls the browser. The grid uses these ~0.4 MP
    thumbnails instead; the lightbox still points at the full-res original.
    """
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)  # bake in rotation so it matches the original
            im.thumbnail((max_px, max_px))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
    except Exception:  # best-effort thumbnail; any decode/encode failure → fall back to src
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


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


def _build_items(photos: Sequence[Photo], originals_root: Path | None) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    missing = 0
    for photo in photos:
        path = _resolve_path(photo, originals_root)
        if path is None:
            missing += 1
            continue
        items.append(
            {
                "filename": photo.filename,
                "src": Path(path).as_uri(),  # full-res original, used only by the lightbox
                "thumb": _thumb_data_uri(path),  # small embedded thumbnail, used by the grid
                "selected": bool(photo.selected),
                "aesthetic": photo.aesthetic_score,
                "emb": _normalize(photo.clip_embedding),
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
        photos = session.scalars(
            select(Photo).where(Photo.album_id == album.id).order_by(Photo.filename)
        ).all()
        # detach a plain snapshot of the album fields we need before the session closes
        snapshot = {"slug": album.slug, "title": album.title}
        return snapshot, _build_items(photos, originals_root)


def render_html(album: dict[str, str], items: list[dict[str, object]]) -> str:
    # `</` is escaped so a filename containing "</script>" can't close the inline
    # <script> and break the page; filenames are still escaped again in the JS
    # captions (esc()) before landing in innerHTML.
    data_json = json.dumps(items).replace("</", "<\\/")
    total = len(items)
    selected = sum(1 for it in items if it["selected"])
    return _TEMPLATE.format(
        title=html.escape(album["title"]),
        slug=html.escape(album["slug"]),
        total=total,
        selected=selected,
        data_json=data_json,
    )


_TEMPLATE = """<!doctype html>
<html lang="en">
<!--
  Photo-selection gallery — generated by scripts/selection_gallery.py from the
  pipeline's Postgres DB. Open locally (file://) to eyeball an album's curated set;
  images are referenced by absolute path on disk. Regenerate with:
      uv run python scripts/selection_gallery.py [album-slug]

  FEATURES
  - Grid of every photo. Selected = green ✓ badge, full colour; unselected = red ✕
    badge, very slightly dimmed + grayscaled, lifting on hover.
  - Sort: filename, aesthetic ↓/↑, selected-first.
  - "Hide/Show unselected" toggle (live count in the header).
  - Lightbox gallery: click any photo → centred modal, full image at original aspect
    ratio (fit to viewport). ← / → (or on-screen ‹ ›) page prev/next in display order
    without closing; Esc / backdrop / ✕ closes. Caption shows selection, filename,
    ★ aesthetic, position (i / n).
  - Focus mode: each photo has a top-right "focus" checkbox. Checking one filters the
    grid to photos within a cosine distance of it (embeddings are L2-normalised, so
    similarity is a dot product computed in the browser — no NxN matrix), sorted
    nearest-first. Each card reveals ★ aesthetic · d(istance) · s(imilarity). Checking
    another photo's box swaps the focus; "Exit focus" (or unchecking) leaves. A photo
    with no CLIP embedding shows a greyed "unfocusable" marker instead of the checkbox.
  - "≤ dist" slider (0–0.30, 0.01 steps) tunes the focus cutoff; re-render is debounced
    300 ms; a hover tooltip previews the value at the cursor before clicking.
  - Compare: in focus mode, non-focused photos also get a "compare" checkbox → opens the
    lightbox with the focused photo and that one side-by-side, showing the cosine distance
    between them. ← / → then cycle the right-hand photo through the current filtered list
    (the left stays the focused anchor).
  - URL sync: sort, hide-unselected, focus (by filename), dist, and compare are mirrored
    into the query string (history.replaceState) and restored on load — views are
    shareable / reload-stable.

  IMPLEMENTATION NOTES
  - Photo data (incl. normalised embeddings) is embedded as JSON below; no network.
  - The grid shows small EXIF-oriented JPEG thumbnails embedded as data: URIs (the
    source photos are ~24 MP — decoding/scaling those in a 277px cell stalls the
    browser). The lightbox alone loads the full-res original (one at a time).
  - Cards are built ONCE by cloning the <template id="cardTpl"> (no HTML-string concat)
    and kept persistent in the DOM with JS references. Filtering/sorting only toggles CSS
    classes (.hidden, .focused, .grid.focusing) and reorders nodes via a single
    documentFragment reflow — no node churn, no innerHTML rebuilds, no image reloads.
  - Filenames + album title/slug are HTML-escaped (esc() in JS, html.escape in Python)
    and `</` in the JSON payload is neutralised, so odd filenames can't break the page.
-->
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Selection · {title}</title>
<style>
  :root {{
    --bg: #0f1115;
    --panel: #171a21;
    --panel-2: #1e222b;
    --text: #e7e9ee;
    --muted: #9aa1ad;
    --line: #2a2f3a;
    --green: #2ecc71;
    --red: #ff5964;
    --accent: #6ea8fe;
    --radius: 12px;
    --ease: cubic-bezier(.22, .61, .36, 1);   /* ease-out: snappy start, smooth finish */
  }}
  * {{ box-sizing: border-box; }}
  [hidden] {{ display: none !important; }}  /* beat the class display rules below */
  body {{
    margin: 0;
    font: 15px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
  }}
  header {{
    position: sticky; top: 0; z-index: 10;
    display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
    padding: 14px 22px;
    background: rgba(15,17,21,.82);
    backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--line);
  }}
  h1 {{ font-size: 17px; margin: 0; font-weight: 600; }}
  h1 small {{ color: var(--muted); font-weight: 400; margin-left: 8px; }}
  .spacer {{ flex: 1; }}
  .stat {{ color: var(--muted); font-size: 13px; }}
  .stat b {{ color: var(--text); }}
  button, select {{
    font: inherit; color: var(--text);
    background: var(--panel-2); border: 1px solid var(--line);
    padding: 7px 13px; border-radius: 9px; cursor: pointer;
    transition: background .18s ease, border-color .18s ease, transform .08s ease;
  }}
  button:hover, select:hover {{ background: #262b36; border-color: #3a414f; }}
  button:active {{ transform: translateY(1px); }}
  button.active {{ background: var(--accent); border-color: var(--accent); color: #0b1020; font-weight: 600; }}
  label.ctl {{ display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 13px; }}
  label.distctl b {{ color: var(--accent); font-variant-numeric: tabular-nums; min-width: 32px; }}
  #distSlider {{ accent-color: var(--accent); cursor: pointer; width: 130px; }}

  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(231px, 1fr));
    gap: 16px;
    padding: 22px;
  }}
  .card {{
    position: relative;
    border-radius: var(--radius);
    overflow: hidden;
    background: var(--panel);
    aspect-ratio: 3 / 2;
    animation: pop .4s var(--ease) both;
    transition: transform .2s var(--ease), box-shadow .2s var(--ease), opacity .3s var(--ease);
  }}
  @keyframes pop {{ from {{ opacity: 0; transform: translateY(10px) scale(.98); }} }}
  .card img {{
    width: 100%; height: 100%; object-fit: cover; display: block;
    transition: transform .35s var(--ease), filter .3s var(--ease);
  }}
  .card {{ cursor: pointer; }}
  .card.hidden {{ display: none; }}   /* filtered out — node stays in the DOM */
  /* unselected: very slight dimming that lifts on hover */
  .card.unselected::after {{
    content: "";
    position: absolute; inset: 0;
    background: rgba(8,10,14,.18);
    transition: opacity .3s var(--ease);
    pointer-events: none;
  }}
  .card.unselected:hover::after {{ opacity: 0; }}
  .card.unselected img {{ filter: grayscale(.12); }}
  .card.unselected:hover img {{ filter: none; }}
  .card:hover {{ transform: translateY(-4px); box-shadow: 0 12px 30px rgba(0,0,0,.5); }}
  .card:hover img {{ transform: scale(1.05); }}

  .badge {{
    position: absolute; top: 10px; left: 10px; z-index: 2;
    width: 30px; height: 30px; border-radius: 50%;
    display: grid; place-items: center;
    font-size: 16px; font-weight: 700; color: #fff;
    box-shadow: 0 2px 8px rgba(0,0,0,.45);
    transition: transform .2s var(--ease);
  }}
  .card:hover .badge {{ transform: scale(1.12); }}
  .badge.tick {{ background: var(--green); }}
  .badge.cross {{ background: var(--red); }}

  /* per-card checkboxes stack top-right: compare on top, focus below — a flex
     column so a hidden one collapses with no leftover gap */
  .tools {{
    position: absolute; top: 8px; right: 8px; z-index: 3;
    display: flex; flex-direction: column; align-items: flex-end; gap: 6px;
  }}
  .focus, .compare {{
    display: inline-flex; align-items: center; gap: 5px;
    padding: 4px 8px; border-radius: 20px;
    background: rgba(10,12,16,.62); backdrop-filter: blur(4px);
    font-size: 11px; font-weight: 600; cursor: pointer;
    transition: opacity .2s var(--ease), background .2s var(--ease);
  }}
  .focus {{ color: #d7dbe3; opacity: 0; }}
  .card:hover .focus, .focus.on {{ opacity: 1; }}
  .focus.on {{ background: var(--accent); color: #0b1020; }}
  .focus input {{ accent-color: var(--accent); margin: 0; cursor: pointer; }}
  /* no-embedding photos: always-visible, greyed, non-interactive marker */
  .focus.unfocusable {{ opacity: .85; color: #7c828d; cursor: not-allowed; }}
  .focus.unfocusable input {{ display: none; }}

  /* compare — only in focus mode, on the non-focused photos */
  .compare {{ display: none; color: #ffd9a6; }}
  .grid.focusing .card:not(.focused) .compare {{ display: inline-flex; }}
  .compare:hover {{ background: rgba(255,170,60,.85); color: #241400; }}
  .compare input {{ accent-color: #ffab3c; margin: 0; cursor: pointer; }}

  /* similarity score revealed under each photo while focus mode is active */
  .simchip {{
    position: absolute; left: 0; right: 0; bottom: 0; z-index: 2;
    padding: 6px 10px;
    background: linear-gradient(transparent, rgba(0,0,0,.82));
    font-size: 12px; color: #eef1f6; font-variant-numeric: tabular-nums;
    display: none; justify-content: space-between; gap: 8px;
  }}
  .simchip .sim {{ display: inline-flex; gap: 8px; align-items: center; }}
  .simchip .saes {{ color: #ffd28a; font-weight: 600; }}
  .simchip .sv {{ color: var(--accent); font-weight: 600; }}
  .grid.focusing .simchip {{ display: flex; }}   /* only in focus mode */
  .grid.focusing .meta {{ display: none; }}
  .card.focused {{ outline: 3px solid var(--accent); outline-offset: -3px; }}

  .meta {{
    position: absolute; left: 0; right: 0; bottom: 0; z-index: 2;
    padding: 20px 12px 9px;
    background: linear-gradient(transparent, rgba(0,0,0,.8));
    font-size: 12px; color: #dfe3ea;
    display: flex; justify-content: space-between; gap: 8px;
    transform: translateY(4px); opacity: .0;
    transition: opacity .25s var(--ease), transform .25s var(--ease);
  }}
  .card:hover .meta {{ opacity: 1; transform: none; }}
  .meta .fn {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .meta .aes {{ color: var(--accent); font-variant-numeric: tabular-nums; font-weight: 600; }}
  .empty {{ padding: 60px; text-align: center; color: var(--muted); }}

  /* lightbox modal — centered, full image, original aspect ratio, fit to view */
  .lightbox {{
    position: fixed; inset: 0; z-index: 100;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 14px; padding: 32px;
    background: rgba(6,8,12,.9);
    backdrop-filter: blur(6px);
    opacity: 0; visibility: hidden;
    transition: opacity .25s var(--ease), visibility .25s var(--ease);
  }}
  .lightbox.open {{ opacity: 1; visibility: visible; }}
  .lightbox img {{
    max-width: 100%; max-height: calc(100vh - 120px);
    width: auto; height: auto; object-fit: contain;
    border-radius: 8px; box-shadow: 0 20px 60px rgba(0,0,0,.6);
    transform: scale(.96); transition: transform .25s var(--ease);
  }}
  .lightbox.open img {{ transform: scale(1); }}
  .lightbox .lbcap {{
    display: flex; align-items: center; gap: 14px;
    color: var(--text); font-size: 14px;
  }}
  .lightbox .lbcap .tag {{
    padding: 3px 10px; border-radius: 20px; font-weight: 600; font-size: 12px;
  }}
  .lightbox .lbcap .tag.sel {{ background: var(--green); color: #06210f; }}
  .lightbox .lbcap .tag.unsel {{ background: var(--red); color: #2a0507; }}
  .lightbox .lbcap .aes {{ color: var(--accent); font-variant-numeric: tabular-nums; }}
  .lightbox .lbcap .idx {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
  .lightbox .close {{
    position: absolute; top: 18px; right: 22px;
    width: 40px; height: 40px; border-radius: 50%;
    font-size: 20px; line-height: 1; padding: 0;
  }}
  .lightbox .nav {{
    position: absolute; top: 50%; transform: translateY(-50%);
    width: 52px; height: 52px; border-radius: 50%;
    font-size: 30px; line-height: 1; padding: 0 0 4px;
    display: grid; place-items: center;
  }}
  .lightbox .nav.prev {{ left: 22px; }}
  .lightbox .nav.next {{ right: 22px; }}
  .lightbox .nav:hover {{ transform: translateY(-50%) scale(1.1); }}

  /* side-by-side compare view inside the lightbox */
  .cmp {{ display: flex; align-items: center; justify-content: center; gap: 20px; width: 100%; }}
  .cmp figure {{ margin: 0; display: flex; flex-direction: column; align-items: center; gap: 10px; max-width: 44%; }}
  .cmp img {{
    max-width: 100%; max-height: calc(100vh - 150px);
    width: auto; height: auto; object-fit: contain;
    border-radius: 8px; box-shadow: 0 20px 60px rgba(0,0,0,.6);
  }}
  .cmp figcaption {{ display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--text); }}
  .cmp .tag {{ padding: 3px 10px; border-radius: 20px; font-weight: 600; font-size: 12px; }}
  .cmp .tag.sel {{ background: var(--green); color: #06210f; }}
  .cmp .tag.unsel {{ background: var(--red); color: #2a0507; }}
  .cmp .tag.foc {{ background: var(--accent); color: #0b1020; }}
  .cmp .aes {{ color: var(--accent); font-variant-numeric: tabular-nums; font-weight: 600; }}
  .cmpmid {{
    display: flex; flex-direction: column; align-items: center; gap: 3px;
    font-variant-numeric: tabular-nums; color: var(--muted); font-size: 12px;
    white-space: nowrap;
  }}
  .cmpmid .big {{ font-size: 26px; font-weight: 700; color: var(--accent); }}
</style>
</head>
<body>
<header>
  <h1>{title} <small>{slug}</small></h1>
  <div class="stat"><b id="shown">{total}</b> shown · <b id="selCount">{selected}</b> selected / {total} total</div>
  <span class="spacer"></span>
  <label class="ctl">Sort
    <select id="sort">
      <option value="filename">filename</option>
      <option value="aes-desc">aesthetic ↓</option>
      <option value="aes-asc">aesthetic ↑</option>
      <option value="sel-first">selected first</option>
    </select>
  </label>
  <button id="toggleUnsel">Hide unselected</button>
  <label class="ctl distctl" id="distCtl" hidden>≤ dist
    <input type="range" id="distSlider" min="0" max="0.3" step="0.01" value="0.3">
    <b id="distVal">0.30</b>
  </label>
  <button id="exitFocus" hidden>✕ Exit focus</button>
</header>
<template id="cardTpl">
  <div class="card">
    <div class="badge"></div>
    <div class="tools">
      <label class="compare" title="Compare with focused photo"><input type="checkbox">compare</label>
      <label class="focus" title="Focus / compare similar"><input type="checkbox">focus</label>
    </div>
    <img loading="lazy" alt="">
    <div class="meta"><span class="fn"></span><span class="aes"></span></div>
    <div class="simchip"><span class="fn"></span><span class="sim"><b class="saes"></b><span class="sv"></span></span></div>
  </div>
</template>
<main>
  <div class="grid" id="grid"></div>
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
    <figure><img id="cmpB" src="" alt=""><figcaption id="cmpBcap"></figcaption></figure>
  </div>
</div>
<script>
const DATA = {data_json};
const grid = document.getElementById('grid');
const sortSel = document.getElementById('sort');
const toggleBtn = document.getElementById('toggleUnsel');
const shownEl = document.getElementById('shown');
let hideUnsel = false;

function aes(x) {{ return (x.aesthetic == null) ? -Infinity : x.aesthetic; }}
const sorters = {{
  'filename': (a, b) => a.filename.localeCompare(b.filename),
  'aes-desc': (a, b) => aes(b) - aes(a),
  'aes-asc':  (a, b) => aes(a) - aes(b),
  'sel-first': (a, b) => (b.selected - a.selected) || (aes(b) - aes(a)),
}};

DATA.forEach((x, i) => {{ x.id = i; }});  // stable id, survives filter/sort
let focusMaxDist = 0.30;                   // cosine distance cutoff (slider-tunable)
let focusId = null;                        // id of the focused photo, or null

// escape data (filenames) before it lands in a caption's innerHTML
const esc = (s) => s.replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));

// cosine similarity of two L2-normalized embeddings === their dot product
function cosine(a, b) {{
  if (!a || !b) return null;
  let dot = 0;
  for (let i = 0; i < a.length; i++) dot += a[i] * b[i];
  return dot;
}}

// the currently visible photos, in display order — what the lightbox pages through
let visibleRows = [];

// Build every card ONCE by cloning a native <template> (no HTML string concat).
// Filtering/sorting later only toggles CSS classes and re-orders persistent nodes
// via a document fragment — no node churn, no innerHTML rebuilds, no image reloads.
const cardTpl = document.getElementById('cardTpl');

function buildCard(x) {{
  const card = cardTpl.content.firstElementChild.cloneNode(true);
  card.classList.add(x.selected ? 'selected' : 'unselected');

  const badge = card.querySelector('.badge');
  badge.classList.add(x.selected ? 'tick' : 'cross');
  badge.textContent = x.selected ? '✓' : '✕';

  const img = card.querySelector('img');
  img.src = x.thumb || x.src;   // grid uses the small embedded thumbnail
  img.alt = x.filename;
  card.querySelectorAll('.fn').forEach(el => {{ el.textContent = x.filename; }});
  const aesTxt = '★ ' + (x.aesthetic == null ? '—' : x.aesthetic.toFixed(2));
  card.querySelector('.meta .aes').textContent = aesTxt;
  card.querySelector('.simchip .saes').textContent = aesTxt;  // keep aesthetic visible in focus mode

  x._card = card;
  x._focusLabel = card.querySelector('.focus');
  x._focusInput = x._focusLabel.querySelector('input');
  const cmpLabel = card.querySelector('.compare');
  x._cmpInput = cmpLabel.querySelector('input');
  x._sv = card.querySelector('.simchip .sv');

  // a photo with no CLIP embedding can't anchor or be scored in focus mode —
  // mark it "unfocusable" (visible + disabled) rather than silently dropping it
  if (!x.emb) {{
    x._focusLabel.classList.add('unfocusable');
    x._focusLabel.lastChild.textContent = 'unfocusable';
    x._focusInput.disabled = true;
    x._focusLabel.title = 'No CLIP embedding — can\\'t compute similarity';
  }}

  x._focusLabel.addEventListener('click', (e) => e.stopPropagation());
  x._focusInput.addEventListener('change', (e) => setFocus(e.target.checked ? x.id : null));
  cmpLabel.addEventListener('click', (e) => e.stopPropagation());
  x._cmpInput.addEventListener('change', (e) => {{
    if (e.target.checked) openCompare(DATA[focusId], x);
    else closeLightbox();
  }});
  card.addEventListener('click', () => {{
    const i = visibleRows.indexOf(x);
    if (i >= 0) openLightbox(i);
  }});
  return card;
}}

// one fragment for the initial build too
const buildFrag = document.createDocumentFragment();
DATA.forEach(x => buildFrag.appendChild(buildCard(x)));
grid.appendChild(buildFrag);

function render() {{
  // a photo with no embedding can't anchor focus mode — treat it as no focus
  const focus = (focusId != null && DATA[focusId].emb) ? DATA[focusId] : null;
  grid.classList.toggle('focusing', !!focus);

  visibleRows = [];

  if (focus) {{
    // focus mode: photos within focusMaxDist of the focused one, nearest first
    const ranked = DATA
      .map(x => ({{ x, dist: 1 - cosine(focus.emb, x.emb) }}))
      .sort((a, b) => a.dist - b.dist);
    ranked.forEach(r => {{
      // the anchor always shows (4-dp-rounded embeddings make its self-distance a hair
      // above 0); others need a finite distance within the cutoff (null emb → NaN → out)
      const x = r.x;
      const vis = x.id === focusId || (Number.isFinite(r.dist) && r.dist <= focusMaxDist);
      x._card.classList.toggle('hidden', !vis);
      x._card.classList.toggle('focused', x.id === focusId);
      x._focusInput.checked = x.id === focusId;
      x._focusLabel.classList.toggle('on', x.id === focusId);
      if (vis) {{
        x._sv.textContent = `d ${{r.dist.toFixed(3)}} · s ${{(1 - r.dist).toFixed(3)}}`;
        visibleRows.push(x);
      }}
    }});
  }} else {{
    const rows = DATA.filter(x => !hideUnsel || x.selected).sort(sorters[sortSel.value]);
    const visibleSet = new Set(rows);
    DATA.forEach(x => {{
      const vis = visibleSet.has(x);
      x._card.classList.toggle('hidden', !vis);
      x._card.classList.remove('focused');
      x._focusInput.checked = false;
      x._focusLabel.classList.remove('on');
    }});
    visibleRows = rows;
  }}

  // reorder WITHOUT moving nodes: set the CSS `order` of each visible card to its rank
  // (grid items honour `order`). Cards stay put in the DOM, so nothing re-parents —
  // no restarted animations, no per-node reflow, just one cheap style recalc + layout.
  visibleRows.forEach((x, i) => {{ x._card.style.order = i; }});

  shownEl.textContent = visibleRows.length;
  document.getElementById('empty').hidden = visibleRows.length > 0;
  exitFocusBtn.hidden = focus == null;
  distCtl.hidden = focus == null;
  syncUrl();
}}

// mirror view state into the URL (?sort=&hide=&focus=&dist=&compare=) so a view is
// shareable / reload-stable; replaceState keeps it out of the back-button history.
function syncUrl() {{
  const p = new URLSearchParams();
  if (sortSel.value !== 'filename') p.set('sort', sortSel.value);
  if (hideUnsel) p.set('hide', '1');
  if (focusId != null && DATA[focusId].emb) {{
    p.set('focus', DATA[focusId].filename);
    if (Math.abs(focusMaxDist - 0.30) > 1e-9) p.set('dist', focusMaxDist.toFixed(2));
  }}
  if (cmpMode && cmpOtherId != null) p.set('compare', DATA[cmpOtherId].filename);
  const qs = p.toString();
  // replaceState can throw in some file:// / sandboxed contexts — never let URL
  // mirroring take down render()
  try {{ history.replaceState(null, '', qs ? '?' + qs : location.pathname); }} catch (e) {{ /* ignore */ }}
}}

const exitFocusBtn = document.getElementById('exitFocus');
const distCtl = document.getElementById('distCtl');
const distSlider = document.getElementById('distSlider');
const distVal = document.getElementById('distVal');
function setFocus(id) {{ focusId = id; render(); }}
exitFocusBtn.addEventListener('click', () => setFocus(null));

// slider: update the label live, but debounce the (heavier) re-render by 300ms
let distTimer = null;
distSlider.addEventListener('input', () => {{
  focusMaxDist = parseFloat(distSlider.value);
  distVal.textContent = focusMaxDist.toFixed(2);
  clearTimeout(distTimer);
  distTimer = setTimeout(render, 300);
}});
// native tooltip previewing the value at the hovered position (before clicking)
distSlider.addEventListener('mousemove', (e) => {{
  const min = parseFloat(distSlider.min), max = parseFloat(distSlider.max);
  const step = parseFloat(distSlider.step) || 0.01;
  const r = distSlider.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  const val = Math.round((min + frac * (max - min)) / step) * step;
  distSlider.title = '≤ ' + val.toFixed(2);
}});

const lightbox = document.getElementById('lightbox');
const lbImg = document.getElementById('lbImg');
const lbCap = document.getElementById('lbCap');
const lbPrev = document.getElementById('lbPrev');
const lbNext = document.getElementById('lbNext');
const lbCmp = document.getElementById('lbCmp');
const singleEls = [lbImg, lbCap, lbPrev, lbNext];
let lbIndex = -1;
let cmpMode = false;
let cmpOtherId = null;  // id of the "other" photo in the compare view (for URL sync)

function setSingleMode() {{
  cmpMode = false;
  cmpOtherId = null;
  lbCmp.hidden = true;
  singleEls.forEach(el => {{ el.hidden = false; }});
}}

function showAt(i) {{
  const n = visibleRows.length;
  if (n === 0) return;
  lbIndex = (i + n) % n;            // wrap around, gallery-style
  const x = visibleRows[lbIndex];
  lbImg.src = x.src;
  lbImg.alt = x.filename;
  const aesTxt = x.aesthetic == null ? '—' : x.aesthetic.toFixed(2);
  lbCap.innerHTML =
    `<span class="tag ${{x.selected ? 'sel' : 'unsel'}}">${{x.selected ? '✓ selected' : '✕ unselected'}}</span>`
    + `<span>${{esc(x.filename)}}</span><span class="aes">★ ${{aesTxt}}</span>`
    + `<span class="idx">${{lbIndex + 1}} / ${{n}}</span>`;
}}
function openLightbox(i) {{ setSingleMode(); showAt(i); lightbox.classList.add('open'); }}

// side-by-side: focused photo (left) vs the compared one (right), with their distance
function cmpCap(x, isFocus) {{
  const aesTxt = x.aesthetic == null ? '—' : x.aesthetic.toFixed(2);
  return (isFocus ? `<span class="tag foc">focused</span>` : '')
    + `<span class="tag ${{x.selected ? 'sel' : 'unsel'}}">${{x.selected ? '✓' : '✕'}}</span>`
    + `<span>${{esc(x.filename)}}</span><span class="aes">★ ${{aesTxt}}</span>`;
}}
function openCompare(focus, other) {{
  if (!focus) return;
  cmpMode = true;
  cmpOtherId = other.id;
  singleEls.forEach(el => {{ el.hidden = true; }});
  lbCmp.hidden = false;
  document.getElementById('cmpA').src = focus.src;
  document.getElementById('cmpB').src = other.src;
  document.getElementById('cmpAcap').innerHTML = cmpCap(focus, true);
  document.getElementById('cmpBcap').innerHTML = cmpCap(other, false);
  const d = 1 - cosine(focus.emb, other.emb);
  document.getElementById('cmpMid').innerHTML =
    `<span class="big">${{d.toFixed(3)}}</span><span>cosine dist</span><span>sim ${{(1 - d).toFixed(3)}}</span>`;
  lightbox.classList.add('open');
  syncUrl();
}}

function closeLightbox() {{
  lightbox.classList.remove('open');
  lbIndex = -1;
  if (cmpMode) {{
    setSingleMode();
    document.querySelectorAll('.compare input:checked').forEach(i => {{ i.checked = false; }});
    syncUrl();  // drop ?compare
  }}
}}
// in compare mode, arrows cycle only the right-hand (compared) photo through the
// current filtered list; the left stays the focused anchor
function cmpStep(delta) {{
  const focus = DATA[focusId];
  if (!focus) return;
  const others = visibleRows.filter(x => x.id !== focusId);
  if (!others.length) return;
  const cur = others.findIndex(x => x.id === cmpOtherId);
  const other = others[((cur < 0 ? 0 : cur + delta) + others.length) % others.length];
  openCompare(focus, other);   // updates cmpB/cmpMid/cmpOtherId + URL
  document.querySelectorAll('.compare input:checked').forEach(i => {{ i.checked = false; }});
  if (other._cmpInput) other._cmpInput.checked = true;
}}
function next() {{ cmpMode ? cmpStep(1) : showAt(lbIndex + 1); }}
function prev() {{ cmpMode ? cmpStep(-1) : showAt(lbIndex - 1); }}

lightbox.addEventListener('click', (e) => {{ if (e.target === lightbox) closeLightbox(); }});
document.getElementById('lbClose').addEventListener('click', closeLightbox);
document.getElementById('lbNext').addEventListener('click', next);
document.getElementById('lbPrev').addEventListener('click', prev);
document.addEventListener('keydown', (e) => {{
  if (!lightbox.classList.contains('open')) return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowRight') next();
  else if (e.key === 'ArrowLeft') prev();
}});

sortSel.addEventListener('change', render);
toggleBtn.addEventListener('click', () => {{
  hideUnsel = !hideUnsel;
  toggleBtn.textContent = hideUnsel ? 'Show unselected' : 'Hide unselected';
  toggleBtn.classList.toggle('active', hideUnsel);
  render();
}});

// restore view state from the URL (?sort=&hide=&focus=&dist=&compare=) on load
function applyUrlState() {{
  const p = new URLSearchParams(location.search);
  if (p.has('sort') && sorters[p.get('sort')]) sortSel.value = p.get('sort');
  if (p.get('hide') === '1') {{
    hideUnsel = true;
    toggleBtn.textContent = 'Show unselected';
    toggleBtn.classList.add('active');
  }}
  const byName = new Map(DATA.map(x => [x.filename, x]));
  const f = byName.get(p.get('focus'));
  if (f && f.emb) focusId = f.id;
  const d = parseFloat(p.get('dist'));
  if (Number.isFinite(d)) {{
    focusMaxDist = Math.round(Math.min(0.30, Math.max(0, d)) / 0.01) * 0.01;  // snap to step
    distSlider.value = focusMaxDist;
    distVal.textContent = focusMaxDist.toFixed(2);
  }}
  // only restore a compare that is a real, scorable, *different* photo
  const c = byName.get(p.get('compare'));
  return (focusId != null && c && c.emb && c.id !== focusId) ? c : null;
}}

const pendingCompare = applyUrlState();
render();
if (pendingCompare) {{
  openCompare(DATA[focusId], pendingCompare);
  if (pendingCompare._cmpInput) pendingCompare._cmpInput.checked = true;
}}
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", nargs="?", help="album slug (default: first with photos)")
    parser.add_argument("-o", "--out", type=Path, help="output HTML path")
    args = parser.parse_args()

    album, items = _collect(args.slug)
    out = args.out or (REPO_ROOT / "data" / "albums" / album["slug"] / "selection-gallery.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(album, items), encoding="utf-8")
    print(f"Wrote {len(items)} photos → {out}")
    print(f"Open: file://{out}")


if __name__ == "__main__":
    main()

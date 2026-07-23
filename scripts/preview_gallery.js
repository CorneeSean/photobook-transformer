// Preview-gallery front-end logic. Extracted from scripts/preview_gallery.py so it
// lives as a real, editable JS file (lint/highlight, no Python brace-escaping).
// The generated HTML defines `const DATA = [...]` inline, then loads this file via a
// classic <script src> — which works over file:// (no server), same as the images.
// Regenerate the HTML with: uv run poe preview [album-slug]

const grid = document.getElementById('grid');
const sortSel = document.getElementById('sort');
const groupSel = document.getElementById('group');
const customCtl = document.getElementById('customCtl');
const gnInput = document.getElementById('gn');
const gUnitSel = document.getElementById('gunit');
const sectionsEl = document.getElementById('sections');
const orderBtn = document.getElementById('groupOrder');
const expandBtn = document.getElementById('expandAll');
const collapseBtn = document.getElementById('collapseAll');
const toggleBtn = document.getElementById('toggleUnsel');
const showChangedBtn = document.getElementById('showChanged');
const shownEl = document.getElementById('shown');
const selCountEl = document.getElementById('selCount');
const chgStatEl = document.getElementById('chgStat');
const chgCountEl = document.getElementById('chgCount');
let hideUnsel = false;
let showChanged = false;                     // filter to only staged (changed-from-DB) photos
let groupDesc = false;                      // section order: false = oldest first
let domGrouped = false;                     // are the cards currently split into sections?
const UNDATED = '\u007f';                    // sorts after any real YYYY-MM(-DD) key -> last

function aes(x) { return (x.aesthetic == null) ? -Infinity : x.aesthetic; }
// undated photos sort to the end in either time direction (±Infinity by direction)
function tsAsc(x) { return (x.ts == null) ? Infinity : x.ts; }
function tsDesc(x) { return (x.ts == null) ? -Infinity : x.ts; }
const sorters = {
  'filename': (a, b) => a.filename.localeCompare(b.filename),
  'aes-desc': (a, b) => aes(b) - aes(a),
  'aes-asc':  (a, b) => aes(a) - aes(b),
  'sel-first': (a, b) => (b.selected - a.selected) || (aes(b) - aes(a)),
  'time-asc': (a, b) => tsAsc(a) - tsAsc(b),
  'time-desc': (a, b) => tsDesc(b) - tsDesc(a),
};

DATA.forEach((x, i) => { x.id = i; });  // stable id, survives filter/sort
// x.selected is the LIVE (effective) selection the whole UI reflects; x.dbSelected is
// the value saved in the DB. Clicking a badge stages a toggle by flipping x.selected —
// a suggestion held in the browser until it's exported + saved. changed() spots the delta.
DATA.forEach(x => { x.dbSelected = x.selected; });
function changed(x) { return x.selected !== x.dbSelected; }
const FOCUS_DIST_DEFAULT = 0.10;           // fresh focus starts tight; slider tunes up to 0.30
let focusMaxDist = FOCUS_DIST_DEFAULT;      // cosine distance cutoff (slider-tunable)
let focusId = null;                        // id of the focused photo, or null

// escape data (filenames) before it lands in a caption's innerHTML
const esc = (s) => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// cosine similarity of two L2-normalized embeddings === their dot product
function cosine(a, b) {
  if (!a || !b) return null;
  let dot = 0;
  for (let i = 0; i < a.length; i++) dot += a[i] * b[i];
  return dot;
}

// the currently visible photos, in display order — what the lightbox pages through
let visibleRows = [];

// Build every card ONCE by cloning a native <template> (no HTML string concat).
// Filtering/sorting later only toggles CSS classes and re-orders persistent nodes
// via a document fragment — no node churn, no innerHTML rebuilds, no image reloads.
const cardTpl = document.getElementById('cardTpl');

// paint a card's badge + classes from its live selection and staged (changed) state
function paintSelection(x) {
  const card = x._card, badge = x._badge, on = x.selected, chg = changed(x);
  card.classList.toggle('selected', on);
  card.classList.toggle('unselected', !on);
  card.classList.toggle('staged', chg);
  badge.classList.toggle('tick', on);
  badge.classList.toggle('cross', !on);
  badge.classList.toggle('staged', chg);
  badge.textContent = on ? '✓' : '✕';
  badge.title = chg
    ? `Staged ${on ? 'select' : 'unselect'} — click to revert (saved: ${x.dbSelected ? 'selected' : 'unselected'})`
    : `Click to stage ${on ? 'unselect' : 'select'}`;
}

// stage/unstage a selection toggle for one photo (a browser-only suggestion)
function toggleStaged(x) {
  x.selected = !x.selected;
  paintSelection(x);
  refreshCounts();
  // re-render when the change can shift the view: a selection filter/sort, or grouped
  // mode (whose per-section "N selected" summaries would otherwise go stale)
  if (hideUnsel || showChanged || domGrouped || sortSel.value === 'sel-first') render();
}

// header tallies: live selected count + how many differ from the DB (staged)
function refreshCounts() {
  const sel = DATA.reduce((n, x) => n + (x.selected ? 1 : 0), 0);
  const chg = DATA.reduce((n, x) => n + (changed(x) ? 1 : 0), 0);
  selCountEl.textContent = sel;
  chgCountEl.textContent = chg;
  chgStatEl.hidden = chg === 0;
}

function buildCard(x) {
  const card = cardTpl.content.firstElementChild.cloneNode(true);
  x._card = card;
  x._badge = card.querySelector('.badge');
  paintSelection(x);   // sets .selected/.unselected/.staged + badge glyph/colour/title
  // click the badge to stage a select/unselect toggle (don't open the lightbox)
  x._badge.addEventListener('click', (e) => { e.stopPropagation(); toggleStaged(x); });

  const img = card.querySelector('img');
  img.src = x.thumb || x.src;   // grid uses the small embedded thumbnail
  img.alt = x.filename;
  card.querySelectorAll('.fn').forEach(el => { el.textContent = x.filename; });
  const aesTxt = '★ ' + (x.aesthetic == null ? '—' : x.aesthetic.toFixed(2));
  card.querySelector('.meta .aes').textContent = aesTxt;
  card.querySelector('.simchip .saes').textContent = aesTxt;  // keep aesthetic visible in focus mode

  x._focusLabel = card.querySelector('.focus');
  x._focusInput = x._focusLabel.querySelector('input');
  const cmpLabel = card.querySelector('.compare');
  x._cmpInput = cmpLabel.querySelector('input');
  x._sv = card.querySelector('.simchip .sv');

  // a photo with no CLIP embedding can't anchor or be scored in focus mode —
  // mark it "unfocusable" (visible + disabled) rather than silently dropping it
  if (!x.emb) {
    x._focusLabel.classList.add('unfocusable');
    x._focusLabel.lastChild.textContent = 'n/a';
    x._focusInput.disabled = true;
    x._focusLabel.title = 'No CLIP embedding — can\'t compute similarity';
  }

  x._focusLabel.addEventListener('click', (e) => e.stopPropagation());
  x._focusInput.addEventListener('change', (e) => setFocus(e.target.checked ? x.id : null));
  cmpLabel.addEventListener('click', (e) => e.stopPropagation());
  x._cmpInput.addEventListener('change', (e) => {
    if (e.target.checked) openCompare(DATA[focusId], x);
    else closeLightbox();
  });
  card.addEventListener('click', () => {
    // in similar mode a click always opens a compare view (never the single lightbox)
    if (focusId != null && DATA[focusId] && DATA[focusId].emb) {
      openCompareFor(x);
    } else {
      const i = visibleRows.indexOf(x);
      if (i >= 0) openLightbox(i);
    }
  });
  return card;
}

// one fragment for the initial build too
const buildFrag = document.createDocumentFragment();
DATA.forEach(x => buildFrag.appendChild(buildCard(x)));
grid.appendChild(buildFrag);

// The grouping interval as [unit, count], or null when ungrouped. The fixed presets
// are just custom intervals of size 1, so all bucketing shares one code path.
function groupSpec() {
  const g = groupSel.value;
  if (g === 'none') return null;
  if (g === 'custom') return [gUnitSel.value, Math.max(1, parseInt(gnInput.value, 10) || 1)];
  return [g, 1];  // 'hour' | 'day' | 'week' | 'month' | 'year'
}

const UNIT_SEC = { minute: 60, hour: 3600, day: 86400, week: 604800 };
const DATE_OPTS = { timeZone: 'UTC', year: 'numeric', month: 'long', day: 'numeric' };
const TIME_OPTS = { timeZone: 'UTC', hour: '2-digit', minute: '2-digit' };
const DOW_OPTS  = { timeZone: 'UTC', weekday: 'long' };

// Start of a photo's bucket as a CIVIL epoch (album-local wall clock encoded as UTC),
// so every calc + format below uses UTC accessors and stays in the album's timezone.
// minute/hour/day/week are fixed spans anchored at the epoch (weeks aligned to Monday);
// month/year use calendar arithmetic. `n` blocks are floored from that anchor.
function bucketStart(lts, unit, n) {
  const d = new Date(lts * 1000);
  if (unit === 'year') return Date.UTC(Math.floor(d.getUTCFullYear() / n) * n, 0, 1) / 1000;
  if (unit === 'month') {
    const mi = Math.floor((d.getUTCFullYear() * 12 + d.getUTCMonth()) / n) * n;
    return Date.UTC(Math.floor(mi / 12), mi % 12, 1) / 1000;
  }
  const anchor = unit === 'week' ? 4 * 86400 : 0;  // 1970-01-01 was a Thursday → align weeks to Monday
  const span = UNIT_SEC[unit] * n;
  return anchor + Math.floor((lts - anchor) / span) * span;
}
function bucketEnd(start, unit, n) {
  const d = new Date(start * 1000);
  if (unit === 'year') return Date.UTC(d.getUTCFullYear() + n, 0, 1) / 1000;
  if (unit === 'month') {
    const mi = d.getUTCFullYear() * 12 + d.getUTCMonth() + n;
    return Date.UTC(Math.floor(mi / 12), mi % 12, 1) / 1000;
  }
  return start + UNIT_SEC[unit] * n;
}

// human {date, dow} label for a bucket, given its civil start epoch + span
function labelFor(start, unit, n) {
  if (start === UNDATED) return { date: 'Undated', dow: '' };
  const s = new Date(start * 1000);
  if (unit === 'minute' || unit === 'hour') {                    // sub-day → date + time band
    const e = new Date(bucketEnd(start, unit, n) * 1000);
    return {
      date: `${s.toLocaleDateString(undefined, DATE_OPTS)} · `
          + `${s.toLocaleTimeString(undefined, TIME_OPTS)}–${e.toLocaleTimeString(undefined, TIME_OPTS)}`,
      dow: s.toLocaleDateString(undefined, DOW_OPTS),
    };
  }
  if (unit === 'month' && n === 1)
    return { date: s.toLocaleDateString(undefined, { timeZone: 'UTC', year: 'numeric', month: 'long' }), dow: '' };
  if (unit === 'year' && n === 1) return { date: String(s.getUTCFullYear()), dow: '' };
  if (unit === 'day' && n === 1)
    return { date: s.toLocaleDateString(undefined, DATE_OPTS), dow: s.toLocaleDateString(undefined, DOW_OPTS) };
  // any multi-unit span → inclusive start–end date range
  const last = new Date((bucketEnd(start, unit, n) - 86400) * 1000);
  return {
    date: `${s.toLocaleDateString(undefined, DATE_OPTS)} – ${last.toLocaleDateString(undefined, DATE_OPTS)}`,
    dow: '',
  };
}

// move every card back into the flat #grid (from wherever grouping parked it).
// A finished CSS animation isn't replayed by reparenting, so this is visually seamless.
function ensureFlatDom() {
  if (!domGrouped) return;
  const frag = document.createDocumentFragment();
  DATA.forEach(x => frag.appendChild(x._card));  // appendChild moves the node
  grid.appendChild(frag);
  sectionsEl.textContent = '';
  sectionsEl.hidden = true;
  grid.hidden = false;
  domGrouped = false;
}

// build collapsible <details> sections and reparent the visible cards into them.
// Sets visibleRows in section-then-row order so the lightbox pages the way it reads.
function renderGrouped() {
  const [unit, n] = groupSpec();
  const rows = DATA.filter(x => (!hideUnsel || x.selected) && (!showChanged || changed(x)))
    .sort(sorters[sortSel.value]);
  DATA.forEach(x => {                 // grouping is a flat-view concept: clear focus visuals
    x._card.classList.remove('focused');
    x._focusInput.checked = false;
    x._focusLabel.classList.remove('on');
    x._card.style.order = '';         // append order governs inside a section
  });

  // key = the bucket's civil start epoch (numeric), or the UNDATED sentinel
  const buckets = new Map();
  for (const x of rows) {
    const key = x.lts == null ? UNDATED : bucketStart(x.lts, unit, n);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(x);
  }
  // dated buckets chronologically (reversed when "newest first"); Undated always trails
  const keys = [...buckets.keys()].filter(k => k !== UNDATED).sort((a, b) => a - b);
  if (groupDesc) keys.reverse();
  if (buckets.has(UNDATED)) keys.push(UNDATED);

  // remember which sections the user had collapsed so a re-render doesn't reopen them
  // (dataset.key is always a string, so compare against String(key))
  const collapsed = new Set(
    [...sectionsEl.querySelectorAll('details.section')].filter(d => !d.open).map(d => d.dataset.key)
  );

  sectionsEl.textContent = '';
  const frag = document.createDocumentFragment();
  for (const key of keys) {
    const group = buckets.get(key);
    const lbl = labelFor(key, unit, n);
    const nSel = group.filter(x => x.selected).length;
    const details = document.createElement('details');
    details.className = 'section';
    details.dataset.key = String(key);
    details.open = !collapsed.has(String(key));
    const summary = document.createElement('summary');
    summary.innerHTML =
      `<span class="chev">▶</span>`
      + `<span class="date">${esc(lbl.date)}<span class="dow">${esc(lbl.dow)}</span></span>`
      + `<span class="spacer"></span>`
      + `<span class="counts"><b>${group.length}</b> photos`
      + (nSel ? ` · <span class="sel"><b>${nSel}</b> selected</span>` : '')
      + `</span>`;
    details.appendChild(summary);
    const g = document.createElement('div');
    g.className = 'grid';
    for (const x of group) {
      x._card.classList.remove('hidden');
      g.appendChild(x._card);          // moves the persistent node into this section
      visibleRows.push(x);
    }
    details.appendChild(g);
    frag.appendChild(details);
  }
  sectionsEl.appendChild(frag);
  grid.hidden = true;
  sectionsEl.hidden = false;
  domGrouped = true;
}

function render() {
  // a photo with no embedding can't anchor focus mode — treat it as no focus
  const focus = (focusId != null && DATA[focusId].emb) ? DATA[focusId] : null;
  grid.classList.toggle('focusing', !!focus);
  // focus is an inherently flat, cross-scene ranked view — grouping is suspended in it
  const grouping = !focus && groupSel.value !== 'none';

  visibleRows = [];

  if (focus) {
    ensureFlatDom();
    // focus mode: photos within focusMaxDist of the focused one, nearest first
    const ranked = DATA
      .map(x => ({ x, dist: 1 - cosine(focus.emb, x.emb) }))
      .sort((a, b) => a.dist - b.dist);
    ranked.forEach(r => {
      // the anchor always shows (4-dp-rounded embeddings make its self-distance a hair
      // above 0); others need a finite distance within the cutoff (null emb → NaN → out)
      const x = r.x;
      const vis = x.id === focusId || (Number.isFinite(r.dist) && r.dist <= focusMaxDist);
      x._card.classList.toggle('hidden', !vis);
      x._card.classList.toggle('focused', x.id === focusId);
      x._focusInput.checked = x.id === focusId;
      x._focusLabel.classList.toggle('on', x.id === focusId);
      if (vis) {
        x._sv.textContent = `sim ${(1 - r.dist).toFixed(3)}`;   // similarity only on the card
        visibleRows.push(x);
      }
    });
  } else if (grouping) {
    renderGrouped();
  } else {
    ensureFlatDom();
    const rows = DATA.filter(x => (!hideUnsel || x.selected) && (!showChanged || changed(x)))
    .sort(sorters[sortSel.value]);
    const visibleSet = new Set(rows);
    DATA.forEach(x => {
      const vis = visibleSet.has(x);
      x._card.classList.toggle('hidden', !vis);
      x._card.classList.remove('focused');
      x._focusInput.checked = false;
      x._focusLabel.classList.remove('on');
    });
    visibleRows = rows;
  }

  // flat views reorder WITHOUT moving nodes: set each visible card's CSS `order` to its
  // rank (grid items honour `order`) — one cheap style recalc, no reparent. Grouped view
  // already placed nodes in section order, so its ordering is the DOM order itself.
  if (!grouping) visibleRows.forEach((x, i) => { x._card.style.order = i; });

  shownEl.textContent = visibleRows.length;
  document.getElementById('empty').hidden = visibleRows.length > 0;
  exitFocusBtn.hidden = focus == null;
  distCtl.hidden = focus == null;
  orderBtn.hidden = expandBtn.hidden = collapseBtn.hidden = !grouping;
  syncUrl();
}

// mirror view state into the URL (?sort=&group=&gu=&gn=&order=&hide=&focus=&dist=&compare=) so
// a view is shareable / reload-stable; replaceState keeps it out of back-button history.
function syncUrl() {
  const p = new URLSearchParams();
  if (sortSel.value !== 'filename') p.set('sort', sortSel.value);
  if (groupSel.value !== 'none') p.set('group', groupSel.value);
  if (groupSel.value === 'custom') {
    p.set('gu', gUnitSel.value);
    p.set('gn', String(Math.max(1, parseInt(gnInput.value, 10) || 1)));
  }
  if (groupSel.value !== 'none' && groupDesc) p.set('order', 'desc');
  if (hideUnsel) p.set('hide', '1');
  if (focusId != null && DATA[focusId].emb) {
    p.set('focus', DATA[focusId].filename);
    if (Math.abs(focusMaxDist - FOCUS_DIST_DEFAULT) > 1e-9) p.set('dist', focusMaxDist.toFixed(2));
  }
  if (cmpMode && cmpOtherId != null) p.set('compare', DATA[cmpOtherId].filename);
  const qs = p.toString();
  // replaceState can throw in some file:// / sandboxed contexts — never let URL
  // mirroring take down render()
  try { history.replaceState(null, '', qs ? '?' + qs : location.pathname); } catch (e) { /* ignore */ }
}

const exitFocusBtn = document.getElementById('exitFocus');
const distCtl = document.getElementById('distCtl');
const distSlider = document.getElementById('distSlider');
const distVal = document.getElementById('distVal');
// Scroll a card just under the sticky header (its "top"). ~72px clears the toolbar.
function scrollCardToTop(card, smooth = true) {
  if (!card) return;
  const y = card.getBoundingClientRect().top + window.scrollY - 72;
  window.scrollTo({ top: Math.max(0, y), behavior: smooth ? 'smooth' : 'auto' });
}

// Entering/switching focus scrolls the focused photo to the top; exiting restores the
// pre-focus view by scrolling back to that same photo in the grid (falling back to the
// saved scroll position if a filter now hides it) — a lightweight view-history.
let focusReturn = null;  // { id, scrollY } captured on first entry into focus
function setFocus(id, scroll = true) {
  if (id != null && focusId == null) {
    focusReturn = { id, scrollY: window.scrollY };
    // each fresh entry into focus resets the cutoff to the tight default
    focusMaxDist = FOCUS_DIST_DEFAULT;
    distSlider.value = FOCUS_DIST_DEFAULT;
    distVal.textContent = FOCUS_DIST_DEFAULT.toFixed(2);
  }
  const returning = id == null ? focusReturn : null;
  focusId = id;
  render();
  if (!scroll) return;   // e.g. "Focus this" from the lightbox — don't move the grid behind it
  if (id != null && DATA[id] && DATA[id].emb) {
    scrollCardToTop(DATA[id]._card);
  } else if (returning) {
    const card = DATA[returning.id] && DATA[returning.id]._card;
    if (card && !card.classList.contains('hidden')) scrollCardToTop(card);
    else window.scrollTo({ top: returning.scrollY, behavior: 'smooth' });
    focusReturn = null;
  }
}
exitFocusBtn.addEventListener('click', () => setFocus(null));

// slider: update the label live, but debounce the (heavier) re-render by 300ms
let distTimer = null;
distSlider.addEventListener('input', () => {
  focusMaxDist = parseFloat(distSlider.value);
  distVal.textContent = focusMaxDist.toFixed(2);
  clearTimeout(distTimer);
  distTimer = setTimeout(render, 300);
});
// native tooltip previewing the value at the hovered position (before clicking)
distSlider.addEventListener('mousemove', (e) => {
  const min = parseFloat(distSlider.min), max = parseFloat(distSlider.max);
  const step = parseFloat(distSlider.step) || 0.01;
  const r = distSlider.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  const val = Math.round((min + frac * (max - min)) / step) * step;
  distSlider.title = '≤ ' + val.toFixed(2);
});

const lightbox = document.getElementById('lightbox');
const lbImg = document.getElementById('lbImg');
const lbCap = document.getElementById('lbCap');
const lbPrev = document.getElementById('lbPrev');
const lbNext = document.getElementById('lbNext');
const lbCmp = document.getElementById('lbCmp');
const cmpIdx = document.getElementById('cmpIdx');
const singleEls = [lbImg, lbCap, lbPrev, lbNext];
let lbIndex = -1;
let cmpMode = false;
let cmpOtherId = null;  // id of the "other" photo in the compare view (for URL sync)

function setSingleMode() {
  cmpMode = false;
  cmpOtherId = null;
  lbCmp.hidden = true;
  cmpIdx.hidden = true;
  singleEls.forEach(el => { el.hidden = false; });
}

// a sel/unsel pill that doubles as a click-to-stage toggle button (data-act="toggle").
// `long` = the wordy single-view label; `side` (A|B) tags which compare photo it is.
function selTag(x, { long = false, side = '' } = {}) {
  const on = x.selected, chg = changed(x);
  const label = long ? (on ? '✓ selected' : '✕ unselected') : (on ? '✓' : '✕');
  const title = chg ? 'Staged — click to revert' : 'Click to stage ' + (on ? 'unselect' : 'select');
  return `<button class="tag ${on ? 'sel' : 'unsel'}${chg ? ' staged' : ''}" data-act="toggle"`
    + `${side ? ` data-side="${side}"` : ''} title="${title}">${label}</button>`;
}

function showAt(i) {
  const n = visibleRows.length;
  if (n === 0) return;
  lbIndex = (i + n) % n;            // wrap around, gallery-style
  const x = visibleRows[lbIndex];
  lbImg.src = x.src;
  lbImg.alt = x.filename;
  const aesTxt = x.aesthetic == null ? '—' : x.aesthetic.toFixed(2);
  const when = x.day ? `${x.day}${x.time ? ' ' + x.time : ''}` : '';
  lbCap.innerHTML =
    selTag(x, { long: true })
    + `<span>${esc(x.filename)}</span>`
    + (when ? `<span class="idx">${esc(when)}</span>` : '')
    + `<span class="aes">★ ${aesTxt}</span>`
    + `<span class="idx">${lbIndex + 1} / ${n}</span>`;
}
function openLightbox(i) { setSingleMode(); showAt(i); lightbox.classList.add('open'); }

// side-by-side: focused photo (left) vs the compared one (right), with their distance
function cmpCap(x, isFocus) {
  const aesTxt = x.aesthetic == null ? '—' : x.aesthetic.toFixed(2);
  return (isFocus ? `<span class="tag foc">focused</span>` : '')
    + selTag(x, { side: isFocus ? 'A' : 'B' })
    + `<span>${esc(x.filename)}</span><span class="aes">★ ${aesTxt}</span>`;
}
function openCompare(focus, other) {
  if (!focus) return;
  cmpMode = true;
  cmpOtherId = other.id;
  singleEls.forEach(el => { el.hidden = true; });
  lbCmp.hidden = false;
  document.getElementById('cmpA').src = focus.src;
  document.getElementById('cmpB').src = other.src;
  document.getElementById('cmpAcap').innerHTML = cmpCap(focus, true);
  document.getElementById('cmpBcap').innerHTML = cmpCap(other, false);
  const d = 1 - cosine(focus.emb, other.emb);
  // similarity is the headline metric; cosine distance is secondary
  document.getElementById('cmpMid').innerHTML =
    `<span class="big">${(1 - d).toFixed(3)}</span><span>similarity</span><span>dist ${d.toFixed(3)}</span>`;
  // right-bottom counter: which neighbour of the similar set we're viewing (e.g. 2 / 10)
  const others = visibleRows.filter(v => v.id !== focus.id);
  const pos = others.findIndex(v => v.id === other.id);
  cmpIdx.hidden = pos < 0;
  if (pos >= 0) cmpIdx.textContent = `${pos + 1} / ${others.length}`;
  lightbox.classList.add('open');
  syncUrl();
}

function closeLightbox() {
  lightbox.classList.remove('open');
  lbIndex = -1;
  if (cmpMode) {
    setSingleMode();
    document.querySelectorAll('.compare input:checked').forEach(i => { i.checked = false; });
    syncUrl();  // drop ?compare
  }
}

// most CLIP-similar photo to `anchor` (excluding itself + unembeddable ones), or null
function nearestNeighbor(anchor) {
  let best = null, bestSim = -Infinity;
  for (const y of DATA) {
    if (y.id === anchor.id || !y.emb) continue;
    const sim = cosine(anchor.emb, y.emb);
    if (sim != null && sim > bestSim) { bestSim = sim; best = y; }
  }
  return best;
}

// promote the compared (right) photo to the focus anchor, then compare it against its
// nearest neighbour — lets you "walk" the similarity graph from the compare view
function focusCompared() {
  const anchor = cmpOtherId == null ? null : DATA[cmpOtherId];
  if (!anchor || !anchor.emb) return;
  const nn = nearestNeighbor(anchor);
  setFocus(anchor.id, false);          // re-focus the grid behind the lightbox, but don't scroll it
  if (nn) {
    openCompare(anchor, nn);
    document.querySelectorAll('.compare input:checked').forEach(i => { i.checked = false; });
    if (nn._cmpInput) nn._cmpInput.checked = true;
  } else {
    const i = visibleRows.indexOf(anchor);   // no scorable neighbour → single view of anchor
    openLightbox(i >= 0 ? i : 0);
  }
}

// In similar mode a click always opens a compare view: anchor (left) vs the clicked
// photo (right). Clicking the anchor itself compares it against its closest neighbour.
function openCompareFor(clicked) {
  const anchor = DATA[focusId];
  if (!anchor) return;
  if (clicked.id === anchor.id) {
    const others = visibleRows.filter(v => v.id !== anchor.id);
    const right = others[0] || nearestNeighbor(anchor);   // closest similar, else global NN
    if (!right) { openLightbox(visibleRows.indexOf(anchor)); return; }  // truly alone
    openCompare(anchor, right);
    if (right._cmpInput) right._cmpInput.checked = true;
  } else {
    openCompare(anchor, clicked);
    if (clicked._cmpInput) clicked._cmpInput.checked = true;
  }
}

// flip BOTH compared photos' selection (staged), then focus the right one — the "the
// right shot wins, swap my pick and keep going" move. focusCompared() re-renders once.
function swapAndFocus() {
  const left = focusId == null ? null : DATA[focusId];
  const right = cmpOtherId == null ? null : DATA[cmpOtherId];
  if (!right || !right.emb) return;
  for (const p of [left, right]) {
    if (!p) continue;
    p.selected = !p.selected;
    paintSelection(p);
  }
  refreshCounts();
  focusCompared();   // promotes right → anchor, compares its nearest neighbour
}

// stage a selection toggle from inside the lightbox, then refresh the open view in place
function toggleFromLightbox(x) {
  if (!x) return;
  toggleStaged(x);                     // flips + repaints the grid card + header counts
  if (cmpMode) {
    if (focusId != null && cmpOtherId != null) openCompare(DATA[focusId], DATA[cmpOtherId]);
  } else {
    const i = visibleRows.indexOf(x);  // keep showing the same photo even if a filter moved it
    showAt(i >= 0 ? i : lbIndex);
  }
}
// in compare mode, arrows cycle only the right-hand (compared) photo through the
// current filtered list; the left stays the focused anchor
function cmpStep(delta) {
  const focus = DATA[focusId];
  if (!focus) return;
  const others = visibleRows.filter(x => x.id !== focusId);
  if (!others.length) return;
  const cur = others.findIndex(x => x.id === cmpOtherId);
  const other = others[((cur < 0 ? 0 : cur + delta) + others.length) % others.length];
  openCompare(focus, other);   // updates cmpB/cmpMid/cmpOtherId + URL
  document.querySelectorAll('.compare input:checked').forEach(i => { i.checked = false; });
  if (other._cmpInput) other._cmpInput.checked = true;
}
function next() { cmpMode ? cmpStep(1) : showAt(lbIndex + 1); }
function prev() { cmpMode ? cmpStep(-1) : showAt(lbIndex - 1); }

lightbox.addEventListener('click', (e) => { if (e.target === lightbox) closeLightbox(); });
document.getElementById('lbClose').addEventListener('click', closeLightbox);
document.getElementById('lbNext').addEventListener('click', next);
document.getElementById('lbPrev').addEventListener('click', prev);
// stage a selection toggle by clicking the sel/unsel pill (single view or either compare side)
lbCap.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="toggle"]') && lbIndex >= 0) toggleFromLightbox(visibleRows[lbIndex]);
});
lbCmp.addEventListener('click', (e) => {
  const t = e.target.closest('[data-act="toggle"]');
  if (!t) return;
  toggleFromLightbox(t.dataset.side === 'A' ? DATA[focusId] : DATA[cmpOtherId]);
});
document.getElementById('cmpFocusBtn').addEventListener('click', focusCompared);
document.getElementById('cmpSwapBtn').addEventListener('click', swapAndFocus);
document.addEventListener('keydown', (e) => {
  if (!lightbox.classList.contains('open')) return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowRight') next();
  else if (e.key === 'ArrowLeft') prev();
  else if (e.key === 's' || e.key === 'S')  // stage-toggle the current / compared photo
    toggleFromLightbox(cmpMode ? DATA[cmpOtherId] : visibleRows[lbIndex]);
  else if ((e.key === 'f' || e.key === 'F') && cmpMode) focusCompared();
});

sortSel.addEventListener('change', render);
groupSel.addEventListener('change', () => {
  customCtl.hidden = groupSel.value !== 'custom';
  render();
});
gnInput.addEventListener('input', render);
gUnitSel.addEventListener('change', render);
orderBtn.addEventListener('click', () => {
  groupDesc = !groupDesc;
  orderBtn.textContent = groupDesc ? '↓ Newest first' : '↑ Oldest first';
  orderBtn.classList.toggle('active', groupDesc);
  render();
});
expandBtn.addEventListener('click', () =>
  sectionsEl.querySelectorAll('details.section').forEach(d => { d.open = true; }));
collapseBtn.addEventListener('click', () =>
  sectionsEl.querySelectorAll('details.section').forEach(d => { d.open = false; }));
toggleBtn.addEventListener('click', () => {
  hideUnsel = !hideUnsel;
  toggleBtn.textContent = hideUnsel ? 'Show unselected' : 'Hide unselected';
  toggleBtn.classList.toggle('active', hideUnsel);
  render();
});
showChangedBtn.addEventListener('click', () => {
  showChanged = !showChanged;
  showChangedBtn.classList.toggle('active', showChanged);
  render();
});

// restore view state from the URL (?sort=&group=&gu=&gn=&order=&hide=&focus=&dist=&compare=) on load
function applyUrlState() {
  const p = new URLSearchParams(location.search);
  if (p.has('sort') && sorters[p.get('sort')]) sortSel.value = p.get('sort');
  const gp = p.get('group');
  if (['hour', 'day', 'week', 'month', 'year', 'custom'].includes(gp)) {
    groupSel.value = gp;
    if (gp === 'custom') {
      customCtl.hidden = false;
      if (['minute', 'hour', 'day', 'week', 'month', 'year'].includes(p.get('gu'))) gUnitSel.value = p.get('gu');
      const gn = parseInt(p.get('gn'), 10);
      if (Number.isFinite(gn) && gn >= 1) gnInput.value = gn;
    }
    if (p.get('order') === 'desc') {
      groupDesc = true;
      orderBtn.textContent = '↓ Newest first';
      orderBtn.classList.add('active');
    }
  }
  if (p.get('hide') === '1') {
    hideUnsel = true;
    toggleBtn.textContent = 'Show unselected';
    toggleBtn.classList.add('active');
  }
  const byName = new Map(DATA.map(x => [x.filename, x]));
  const f = byName.get(p.get('focus'));
  if (f && f.emb) focusId = f.id;
  const d = parseFloat(p.get('dist'));
  if (Number.isFinite(d)) {
    focusMaxDist = Math.round(Math.min(0.30, Math.max(0, d)) / 0.01) * 0.01;  // snap to step
    distSlider.value = focusMaxDist;
    distVal.textContent = focusMaxDist.toFixed(2);
  }
  // only restore a compare that is a real, scorable, *different* photo
  const c = byName.get(p.get('compare'));
  return (focusId != null && c && c.emb && c.id !== focusId) ? c : null;
}

const pendingCompare = applyUrlState();
render();
if (pendingCompare) {
  openCompare(DATA[focusId], pendingCompare);
  if (pendingCompare._cmpInput) pendingCompare._cmpInput.checked = true;
}

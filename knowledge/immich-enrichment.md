---
type: Playbook
title: Immich enrichment — build order (steps 17+)
description: Adopt Immich API surface that didn't exist when steps 4–5 were built — faces/people and lead-face prioritization first, then rating/OCR/geocoding — to add curation signals without new local ML dependencies.
tags: [playbook, build-order, immich, faces, people, lead-faces, select, caption, enrichment]
timestamp: 2026-07-14
---

# Immich enrichment — build order (steps 17+)

A follow-on to the [build order](/build-order.md), which ended at step 16 (build
complete). The Immich REST API has grown since the [Immich client](/stages/sync.md)
was built at steps 4–5 (pinned then to OpenAPI **v3.0.2**; Immich is now ~v1.126+).
Three capabilities are now exposed that the pipeline could not use before:

| New Immich surface | Endpoint | What it buys us |
|---|---|---|
| **Face detection + people** | `GET /faces?id={assetId}`, `GET /people` | Portrait-vs-landscape signal, per-person coverage in `select`, name grounding in `caption` — **no local face-detection dependency**. |
| **Lead-face matching** | `GET /faces?id={referenceAssetId}` | Point at a reference photo of the key subjects (the couple, the baby); `select` *prioritizes* photos of them — see [Lead faces](#lead-faces-priority-subjects). |
| **Manual star rating** | `rating` field on assets (−1…5) | Honor a user's own curation as a `select` signal. |
| **OCR text** | `GET /assets/{id}/ocr` | Minor caption/filter signal (signs, menus). |
| **Reverse-geocoded place** | `city`/`state`/`country` on `exifInfo` | Place *names* for `cluster`/`caption` where GPS exists, without our own geocoder. |

**What is still NOT exposed (unchanged):** CLIP embeddings, and any
asset-to-asset visual-similarity endpoint. Smart search only accepts a *text*
query and returns *assets*; the vector never crosses the wire. So `quality`'s
`open_clip` computation and the `pgvector` behind `select`/`cluster` stay
necessary — see [Immich API notes](/immich-api.md) Open Q1. This effort adds
*new signals*; it removes no existing custom code except a hypothetical future
face detector.

## Spike findings (verified against the live docs + current code)

**Client (`src/album/immich/client.py`).** A single-purpose HTTP wrapper. Adding
faces is one new Pydantic model + one method, mirroring `get_duplicates`:

```python
class ImmichFace(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    person_id: str | None = Field(default=None, alias="personId")   # from person.id
    person_name: str | None = Field(default=None, alias="personName") # from person.name
    source_type: str | None = Field(default=None, alias="sourceType") # machine-learning|exif|manual

def get_faces(self, asset_id: str) -> list[ImmichFace]:
    """Detected faces for an asset, via GET /faces?id={asset_id}."""
    response = self._client.get("/faces", params={"id": asset_id})
    response.raise_for_status()
    return [ImmichFace.model_validate(f) for f in response.json()]
```

The real `AssetFaceResponseDto` carries `boundingBoxX1..Y2`, `imageWidth/Height`,
`id`, `sourceType`, and a nested `person: {id, name, birthDate, isHidden,
thumbnailPath}`. We only need `person.id`/`person.name` and `sourceType`; flatten
the rest away at the boundary (keep the pipeline's model lean, same as
`ImmichAsset` today). `person` is `null` for a detected-but-unassigned face —
that's a real face (counts toward `face_count`) with no `person_id`.

**Sync (`src/album/stages/sync.py`).** `sync` is already the boundary and already
loops every asset. Faces need **one extra call per asset** — an N+1 over the
album. That's the notable cost; `get_album_assets` is a single paginated call
today. Mitigations, in order of preference: (a) accept it — `sync` is a
one-per-album batch step behind Prefect retries, and face rows are cheap; (b) fetch
faces only for assets we'll plausibly select (but `select` runs *after* `sync`, so
this needs reordering — avoid); (c) if Immich later exposes faces inline on
`search/metadata`, drop the extra call. Recommend (a). Extend the `AssetSource`
Protocol with `get_faces`; the fake in tests returns canned faces.

**Select (`src/album/stages/select.py`).** Today's MMR ranks by `aesthetic_score`
and rejects near-duplicates (`clip_embedding` cosine) and Immich duplicate groups.
Face data slots in as **two independent, additive rules**, neither of which breaks
the genericity guard (an album with zero recognized faces must behave exactly as
today):

1. *Coverage* — after the greedy pass, if some `person_id` present in the scene
   never made it into `picked`, admit the highest-aesthetic photo containing that
   person (subject to the same duplicate/similarity guards). Ensures "everyone
   appears" without over-selecting.
2. *Tie-break / soft boost* — when aesthetic scores are close, prefer the photo
   with more recognized people (a group shot over an incidental background). Keep
   this as a ranking key, not a hard filter, so a segment-less landscape album is
   unaffected.

Both read only from the DB — no new interface needed, and the stage stays
in-memory-SQLite testable. `person_ids == []` (or all-NULL) ⇒ current behavior,
byte-for-byte.

### Lead faces (priority subjects)

The user supplies a **reference picture of the key people** (the couple, the
baby, the guest of honour); `select` should recognize them across the album and
prioritize photos they appear in.

**The constraint that shapes the design:** Immich exposes *no face-embedding
vectors* over the API — face embeddings live in internal tables, exactly like
CLIP embeddings ([Open Q1](/immich-api.md)). So matching an arbitrary reference
image to library faces *ourselves* would mean adding a face-recognition model
(ArcFace/InsightFace + weights) — a heavy new dependency, precisely the kind of
reimplementation this project delegates to Immich. **Don't.**

**Instead, let Immich's own recognition do the matching.** Immich already clusters
every library face into `Person` entities. The reference picture is just a way to
*name which people are the leads* — and if that picture is (or is uploaded) in
Immich, Immich has already recognized its faces. So the bridge is one call:

```
reference picture ──▶ GET /faces?id={reference_asset_id} ──▶ [person_id, …]  = the lead people
```

Those `person_id`s are the leads. No new model, no vector math on our side — we
reuse the same `get_faces` from the spike above. Two ways to point at the
reference in `album.yml`:

```yaml
lead_faces:
  reference_image: leads.jpg            # PRIMARY — a local photo of the leads, resolved
                                        # next to album.yml. Uploaded to Immich (which runs
                                        # face detection on import), then its faces are read.
  # reference_asset_id: 3fa85f64-…      # or point at an asset already in Immich (no upload)
  # person_ids: [3fa85f64-…, …]         # or name the Immich people directly, skipping a photo
```

**`reference_image` flow** (the chosen primary path): resolve the file relative
to `album.yml`; `POST /assets` to upload it to the album's Immich instance; poll
until Immich's face-detection job has run on it (faces appear asynchronously
after import — don't read faces synchronously right after upload, or you'll get
zero); then `GET /faces?id={uploaded_asset_id}`. This means the client grows an
upload method and the resolver must **tolerate the async gap** (retry/backoff
with a timeout, surfaced as a clear error if detection never completes).
Idempotent: re-uploading the same reference on re-run must not create a second
Immich asset (dedupe on Immich's `bulk-upload-check` sha1, or cache the resolved
`reference_asset_id`). The other two inputs skip upload entirely and go straight
to face-reading / pass-through.

Resolution (a small helper in `definition.py`/`sync`) turns whichever input into a
set of lead Immich person IDs and marks the matching `Person` rows
`is_lead=True`. If a lead face in the reference is *unassigned* in Immich (a
recognized face not yet promoted to a named Person), flag it: Immich needs the
person confirmed once in its UI before cross-album matching works — surface that
rather than silently missing the subject.

**How `select` prioritizes leads** (strictly stronger than the generic coverage
rule above, and still graceful):

1. **Guaranteed coverage** — every scene that contains a lead must select at
   least one photo of that lead (subject to the blur floor / duplicate guards).
   A lead is never culled as a near-duplicate of a *non*-lead.
2. **Ranking boost** — leads outrank the group-shot tie-break: among comparable
   photos, the one with a lead wins; a strong solo of a lead beats an incidental
   crowd shot.
3. **Graceful, opt-in** — no `lead_faces` configured ⇒ behaves exactly as the
   generic people-aware `select` (which in turn reduces to today's behavior when
   there are no faces at all). Genericity guard holds at both levels.

Keep "boost", not "filter": a book of *only* the leads is worse than a book that
leads with them but still tells the album's story. Expose the boost strength as a
tunable (config), same pattern as `select`'s similarity `threshold`.

**Caption.** `person_name`s for a photo's people become another grounding input
to the shared analysis call, exactly like segment name/place and `Album.meta`
today (see [`caption`](/stages/caption.md)) — *"Alex and Sam at the harbour in
Lisbon"*. Names are opt-in grounding, never required.

## Schema additions

> **To the implementer:** the schema and `album.yml` shapes below are a worked
> *proposal*, not a contract. They exist to make the design concrete and to show
> the columns each stage needs to reason about — if a cleaner shape emerges while
> building (e.g. a normalized `photo_faces` join instead of `Photo.person_ids`
> JSON, or a different `lead_faces` key), take it. Keep the *intent* (structured,
> reason-about-able, nullable-for-resumability, genericity-guard-preserving); the
> exact columns are yours to improve.

Follows the [data model](/data-model.md) philosophy — structured columns stages
can *reason about*, not an opaque dict. Two new `Photo` columns plus a small
album-scoped `Person` cache (so `caption` can resolve id → name, and so a
person's name surviving an Immich merge/rename is a re-`sync` away). `is_lead`
marks priority subjects resolved from `album.yml`'s `lead_faces`:

```python
# --- Photo, populated by `sync` (faces) ---
face_count: Mapped[int | None]                                   # portrait vs landscape signal
person_ids: Mapped[list[str] | None] = mapped_column(JSON)       # Immich person UUIDs present (assigned faces only)

# --- new album-scoped cache table ---
class Person(Base):
    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("album_id", "immich_person_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("albums.id"), index=True)
    immich_person_id: Mapped[str]
    name: Mapped[str | None]                                      # None ⇒ recognized but unnamed in Immich
    is_lead: Mapped[bool] = mapped_column(default=False)          # a priority subject (from album.yml lead_faces)
```

`person_ids` is a JSON array of *stable Immich UUIDs* (not names) — the value
`select` reasons about structurally (set membership / coverage), analogous to
`duplicate_group_id`. `caption` joins it to `Person.name`. Both new `Photo`
columns are nullable: `NULL` means `sync` hasn't fetched faces yet
(resumability invariant preserved). Optional later columns from the same effort:
`rating` (int, from the asset), `place_city`/`place_state`/`place_country`
(from `exifInfo`), `ocr_text` (str).

## The steps

Each follows the [implementation workflow](/implementation-workflow.md) (TDD,
sub-agent review, `uv run poe check` green, then squash-merge; the human-review
gate stays waived per the step-4-onward process note in
[progress](/progress.md)). Delivered in order — 17 and 18 are the core; 19 and 20
are independent add-ons.

**17. Faces in the client + `sync` (+ schema).** *Test first*, against a recorded
`/faces` fixture: `ImmichFace` parses (assigned face, unassigned `person: null`,
`sourceType` variants); `get_faces` follows the `?id=` contract. Add the alembic
migration (`Photo.face_count`, `Photo.person_ids`, `people` table). Extend the
`AssetSource` Protocol and `sync` to fetch faces per asset and populate
`face_count`/`person_ids` and upsert `Person` rows — idempotently (re-sync
updates in place, keyed on `immich_person_id`), and **failure-isolated** (a face
call failing for one asset leaves its columns `NULL`, never aborts the album,
mirroring the ambiguous-timestamp path). *Test:* re-running `sync` doesn't
duplicate `Person` rows; an asset with zero faces lands `face_count=0`,
`person_ids=[]`; an unassigned face counts but adds no `person_id`. **Commit.**

**17b. Lead-face resolution.** Add `lead_faces` to `album.yml` and resolve it
(in `definition.py`/`sync`) to a set of lead Immich person IDs, marking those
`Person` rows `is_lead=True` — see [Lead faces](#lead-faces-priority-subjects).
Primary input is **`reference_image`** (a local photo of the leads): the client
grows an upload method (`POST /assets`), and the resolver uploads the file,
**waits out Immich's async face-detection job** (retry/backoff + timeout), then
reads the uploaded asset's faces. *Test first*, against recorded fixtures:
`reference_image` uploads once and resolves to the right person IDs; a re-run does
**not** create a second Immich asset (idempotent upload); the resolver retries
while detection is pending and errors clearly if it never completes;
`reference_asset_id` and `person_ids` resolve without any upload; a reference face
unassigned in Immich is surfaced as a warning, not a silent miss; no `lead_faces`
key ⇒ no leads, no error. **Commit.**

**18. People-aware + lead-aware `select`.** *Test first, thoroughly* (this is the
stage the [build order](/build-order.md) calls the most important to test): the
coverage rule admits a missing person's best shot; the tie-break prefers group
shots only when aesthetic scores are close; **leads get guaranteed per-scene
coverage and outrank the generic tie-break**, and a lead is never dropped as a
near-duplicate of a non-lead; **an album with no recognized faces (and none with
no `lead_faces`) selects byte-for-byte as before** (genericity guard — assert
against the step-16 wedding and a segment-less fixture); coverage still honors
duplicate/similarity guards (no near-dup admitted just to cover a person). The
lead boost strength is config-tunable, like `select`'s `threshold`. All on
in-memory SQLite with similarity stubbed. **Commit.**

**19. Name-grounded `caption` (optional).** *Test first* with `TestModel`: a photo
with `person_ids` resolving to `Person.name`s passes those names in the grounding
context; a photo with unnamed/no people still captions cleanly (no "None"
leaking into the prompt). Reuses the shared analysis call — no second round-trip.
**Commit.**

**20. Rating / OCR / geocoded place (optional, small).** *Test first*: `sync`
reads `rating`, `exifInfo.city/state/country`, and (if wired) OCR text into their
nullable columns; `select` may use `rating` as an additional soft signal;
`caption`/`cluster` may interpolate the place name. Each is independently
skippable. **Commit.**

## Docs to update alongside

- **[Immich API notes](/immich-api.md)** — it's pinned to the stale v3.0.2
  snapshot (pre-faces-API). Add the faces/people, rating, OCR, and geocoding
  surface; keep the Open Q1 "no embeddings, no asset-similarity endpoint" answer,
  now re-confirmed 2026-07-14.
- **[Data model](/data-model.md)** — the new `Photo` columns + `Person` table
  (incl. `is_lead`).
- **[Album definition](/album-definition.md)** — the new `lead_faces` block in
  `album.yml`.
- **[stages/select.md](/stages/select.md)** and **[stages/caption.md](/stages/caption.md)**
  — the new signals.
- **[stages/index.md](/stages/index.md)** — note faces/people join `dedupe` as
  Immich-provided ML the pipeline consumes rather than computes.
</content>
</invoke>

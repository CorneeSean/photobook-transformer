"""Build a browsable cat photo book from the committed sample data, offline.

Reproducible demo of the real pipeline's back half: it seeds an ``Album`` + 5
time/place ``Segment``s + 30 ``Photo`` rows from
``tests/data/cat-album/manifest.json`` (photos already on disk under
``photos/``), then runs the actual [`cluster`](../src/album/stages/cluster.py)
and [`assemble`](../src/album/stages/assemble.py) stages and the default
[`HtmlPrinter`](../src/album/printers/html.py) — the same code a live
``album run`` uses. The front-half stages that need external services (``sync``
from Immich, ``quality``'s scorer, ``enhance``/``render`` via darktable,
``caption`` via Claude) are stood in for by the manifest: every photo arrives
pre-``rendered_path``'d at its on-disk file and pre-captioned, and all are marked
``selected`` (curation via ``select`` needs the deferred quality scores).

`cluster` re-derives each photo's segment purely from its timestamp — the
manifest's ``segment_hint`` is only for humans — so the 5 categories are imposed
by the real stage, not hand-placed.

Run: ``uv run python scripts/build_cat_album.py`` → writes the book to
``tests/data/cat-album/book/index.html`` (open it in a browser).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from album.db.models import Album, Base, Photo, Segment
from album.printers.html import HtmlPrinter
from album.stages.assemble import assemble
from album.stages.cluster import cluster

_ROOT = Path(__file__).resolve().parents[1]
_DATA = _ROOT / "tests" / "data" / "cat-album"


def _seed(session: Session, manifest: dict[str, object]) -> int:
    album_spec = manifest["album"]
    assert isinstance(album_spec, dict)
    album = Album(slug=str(album_spec["slug"]), title=str(album_spec["title"]))

    segments = manifest["segments"]
    assert isinstance(segments, list)
    for spec in segments:
        album.segments.append(
            Segment(
                position=int(spec["position"]),
                label=str(spec["label"]),
                place=str(spec["place"]),
                start_at=datetime.fromisoformat(str(spec["start_at"])),
                end_at=datetime.fromisoformat(str(spec["end_at"])),
            )
        )

    photos = manifest["photos"]
    assert isinstance(photos, list)
    for i, spec in enumerate(photos):
        image_path = _DATA / "photos" / str(spec["file"])
        album.photos.append(
            Photo(
                immich_asset_id=f"cat-{i:02d}",
                filename=str(spec["file"]),
                timestamp=datetime.fromisoformat(str(spec["timestamp"])),
                gps_lat=float(spec["lat"]),
                gps_lon=float(spec["lon"]),
                caption=str(spec["caption"]),
                rendered_path=str(image_path.resolve()),
                selected=True,  # skip `select` curation (needs deferred quality scores)
            )
        )

    session.add(album)
    session.commit()
    assert album.id is not None
    return album.id


def main() -> None:
    manifest = json.loads((_DATA / "manifest.json").read_text())

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        album_id = _seed(session, manifest)

        cluster_result = cluster(album_id, session)
        session.commit()
        print(
            f"cluster: {cluster_result.segments_assigned} photos into segments, "
            f"{cluster_result.scenes} scenes "
            f"(out-of-range={len(cluster_result.out_of_range)}, "
            f"undated={len(cluster_result.undated)})"
        )

        # assemble writes <output_dir>/<slug>/assembled.pdf and sets page_number;
        # HtmlPrinter writes <output_dir>/<slug>/book/index.html.
        assemble_result = assemble(album_id, session, _DATA.parent)
        session.commit()
        print(
            f"assemble: {assemble_result.photos_placed} photos on "
            f"{assemble_result.pages} pages → {assemble_result.output_path}"
        )

        album = session.get(Album, album_id)
        assert album is not None
        print_result = HtmlPrinter(output_dir=_DATA.parent).print(album, session)
        print(f"book:   {print_result.output_ref}  ({print_result.detail})")


if __name__ == "__main__":
    main()

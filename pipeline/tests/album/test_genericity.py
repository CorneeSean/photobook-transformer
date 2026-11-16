"""Build step 16: prove the Album/Segment abstraction is actually generic.

``data/albums/smith-wedding/album.yml`` is a real, shipped album definition —
structurally different from the trip-shaped ``honeymoon-2026`` that every
other stage's tests and the step-14 live run exercised: hour-scale segments
instead of day-scale, a single day instead of several. Per
knowledge/foundations/domain-model.md and knowledge/build/build-order.md step 16, adding it must
require *only* this YAML file — no code change. This test loads the real
shipped file (not a copy under tests/fixtures/) through the same
``get_or_create_album`` bootstrap ``flow.pipeline`` uses, so drift between the
shipped file and the expected shape fails the test gate (`poe check`).

The full live run (real Immich sync, real Postgres+Prefect) is covered by
manual verification in this step's commit body, mirroring step 14 — no
reachable Immich instance in this environment, same deferral as steps
4-5/9/11/12/14/15 (see knowledge/build/progress.md).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from album.analysis.captioner import build_captioner
from album.cli.runner import run_pipelines
from album.cli.sources import prepared_from_outcome
from album.db.models import Album, Segment
from album.definition import AlbumDefinition
from album.flow import get_or_create_album
from album.ingest import DirectoryIngestor, _ingest_all
from album.manifest import load_manifest
from tests.doubles import DatingIngestClient

_SRC_ALBUM = Path(__file__).resolve().parents[2] / "src" / "album"
_FIXTURE_TREE = Path(__file__).resolve().parents[1] / "fixtures" / "manifest_tree"

# The folder-structure-driven ingestion modules (step 32–34) must stay free of
# occasion vocabulary — the genericity rule (folder-structure.md), enforced here in
# tests rather than prose. Word-boundary so it can't false-match inside identifiers
# like ``localDateTime``; scoped to these modules because the rest of src/ legitimately
# *discusses* genericity ("nothing here knows the occasion") and uses "round-trip".
# Caveat: ``\btrip\b`` also matches "trip" inside "round-trip" (hyphen is a word
# boundary) — fine while these three modules avoid that idiom; tighten if one adopts it.
_FOLDER_INGESTION_MODULES = ("manifest.py", "text.py", "ingest.py")
_OCCASION_TOKEN = re.compile(r"\b(?:trips?|itinerar(?:y|ies)|occasions?|days?)\b", re.IGNORECASE)


class TestGenericity:
    """The shipped wedding album must bootstrap from YAML alone — no code change."""

    def test_the_shipped_wedding_album_bootstraps_with_the_expected_shape(
        self,
        session: Session,
    ) -> None:
        album_id = get_or_create_album(session, "smith-wedding")

        album = session.get(Album, album_id)
        assert album is not None
        assert album.slug == "smith-wedding"
        assert album.immich_album_id == "immich-smith-wedding-2026"

        segments = (
            session.query(Segment).filter_by(album_id=album_id).order_by(Segment.position).all()
        )
        assert [s.label for s in segments] == ["Getting ready", "Ceremony", "Reception"]
        assert [s.place for s in segments] == ["The Old Rectory", "St. Mary's", "The Barn"]

        # Hour-scale, not day-scale like honeymoon-2026's segments.
        for segment in segments:
            assert (segment.end_at - segment.start_at).total_seconds() < 24 * 3600

        # A single day, not several.
        assert {s.start_at.date() for s in segments} == {datetime(2026, 8, 15, tzinfo=UTC).date()}

        # Europe/London (+01:00 in August) normalized to UTC.
        assert segments[0].start_at == datetime(2026, 8, 15, 8, 0, 0, tzinfo=UTC)


class TestManifestGenericity:
    """The folder-driven ingestion modules must carry no occasion vocabulary — a
    leak would mean the manifest path grew an album-type-specific concept."""

    @pytest.mark.parametrize("module", _FOLDER_INGESTION_MODULES)
    def test_module_uses_no_occasion_vocabulary(self, module: str) -> None:
        source = (_SRC_ALBUM / module).read_text()
        leaked = _OCCASION_TOKEN.findall(source)
        assert not leaked, f"occasion-specific tokens leaked into src/album/{module}: {leaked}"


class TestManifestFanOutEndToEnd:
    """Prove the whole fan-out on a real fixture tree: one `ingest.yml` → N albums
    → N materialized `album.yml` files → N pipeline runs, over a fake Immich client
    (no live services). Two different album *shapes* — a folder-segmented one and a
    flat one — prove the manifest path needs no per-album-type code, the
    `smith-wedding` genericity precedent applied to fan-out."""

    # Capture times keyed by fixture filename → deterministic folder windows.
    _TIMES = {
        "01.jpg": datetime(2026, 6, 1, 9, 0),
        "02.jpg": datetime(2026, 6, 1, 11, 0),  # morning-harbour → [09:00, 11:00]
        "03.jpg": datetime(2026, 6, 1, 14, 0),  # old-town (single instant → +1s)
        "a.jpg": datetime(2026, 6, 2, 9, 0),
        "b.jpg": datetime(2026, 6, 2, 10, 0),  # garden-party photos (no segments)
    }

    def test_manifest_fans_a_tree_into_albums_and_pipeline_runs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        manifest = load_manifest(_FIXTURE_TREE / "ingest.yml")
        ingestor = DirectoryIngestor(
            DatingIngestClient(self._TIMES),
            albums_dir=tmp_path,
            poll_timeout_s=1.0,
            sleep=lambda _s: None,
        )

        outcomes = _ingest_all(ingestor, _FIXTURE_TREE, manifest, timezone=None, force=False)

        # N declared albums → N outcomes, each ingested (a slug, no error), in order.
        assert [o.slug for o in outcomes] == ["coastal-walk", "garden-party"]
        assert all(o.error is None for o in outcomes)

        # The folder-derived album carries its two windowed segments (labels defaulted
        # from folder names, chronological)...
        coastal = AlbumDefinition().load(tmp_path / "coastal-walk" / "album.yml")
        assert [s.label for s in coastal.segments] == ["Morning Harbour", "Old Town"]
        assert coastal.segments[0].start_at == datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        assert coastal.segments[0].end_at == datetime(2026, 6, 1, 11, 0, tzinfo=UTC)
        # ...while the flat album folder materializes zero segments (segments-optional
        # end-to-end).
        garden = AlbumDefinition().load(tmp_path / "garden-party" / "album.yml")
        assert garden.segments == []

        # The CLI runs the pipeline exactly once per ingested slug (the fan-out
        # outcomes are handed to the shared pipeline driver as prepared albums).
        ran: list[str] = []
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **k: ran.append(k["album_slug"]))
        books, failures = run_pipelines(
            [prepared_from_outcome(outcome) for outcome in outcomes],
            build_captioner("template"),
            force=False,
        )
        assert ran == ["coastal-walk", "garden-party"]
        assert books == ["coastal-walk", "garden-party"]
        assert failures == []

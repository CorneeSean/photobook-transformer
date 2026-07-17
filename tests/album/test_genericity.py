"""Build step 16: prove the Album/Segment abstraction is actually generic.

``data/albums/smith-wedding/album.yml`` is a real, shipped album definition —
structurally different from the trip-shaped ``honeymoon-2026`` that every
other stage's tests and the step-14 live run exercised: hour-scale segments
instead of day-scale, a single day instead of several. Per
knowledge/foundations/domain-model.md and knowledge/build/build-order.md step 16, adding it must
require *only* this YAML file — no code change. This test loads the real
shipped file (not a copy under tests/fixtures/) through the same
``get_or_create_album`` bootstrap ``flow.pipeline`` uses, so drift between the
shipped file and the expected shape fails CI.

The full live run (real Immich sync, real Postgres+Prefect) is covered by
manual verification in this step's commit body, mirroring step 14 — no
reachable Immich instance in this environment, same deferral as steps
4-5/9/11/12/14/15 (see knowledge/build/progress.md).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from album.db.models import Album, Segment
from album.flow import get_or_create_album


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

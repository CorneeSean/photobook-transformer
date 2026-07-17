"""Tests for :class:`~album.immich.client.ImmichClient` against recorded HTTP
fixtures (respx, not a live server).

Fixtures under tests/fixtures/immich/ are schema-accurate to the Immich OpenAPI
spec (v3.0.2); see knowledge/reference/immich-api.md. They should be re-verified against a
live instance on the first real sync run.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from album.immich.client import ImmichClient

# Exercises the real HTTP client at its transport boundary (respx-mocked), not
# pure logic — grouped with `integration` so `poe test-unit` skips it.
pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "immich"
_BASE_URL = "http://immich.test/api"


def _fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text())


class TestImmichClient:
    """`ImmichClient`: pagination, parsing, album creation, job-queue counts,
    auth header, and error propagation."""

    @pytest.fixture
    def client(self) -> ImmichClient:
        return ImmichClient(_BASE_URL, "test-api-key")

    @respx.mock
    def test_get_album_assets_follows_pagination(self, client: ImmichClient) -> None:
        route = respx.post(f"{_BASE_URL}/search/metadata").mock(
            side_effect=[
                httpx.Response(200, json=_fixture("search_metadata_page1.json")),
                httpx.Response(200, json=_fixture("search_metadata_page2.json")),
            ],
        )

        assets = client.get_album_assets("album-123")

        assert route.call_count == 2
        assert [a.id for a in assets] == [
            "a1111111-1111-4111-8111-111111111111",
            "a2222222-2222-4222-8222-222222222222",
        ]
        # Second request asked for page 2 (the nextPage from page 1).
        assert json.loads(route.calls[1].request.content)["page"] == 2
        # Album id scopes the search.
        assert json.loads(route.calls[0].request.content)["albumIds"] == ["album-123"]

    @respx.mock
    def test_parses_exif(self, client: ImmichClient) -> None:
        respx.get(f"{_BASE_URL}/assets/a2222222-2222-4222-8222-222222222222").mock(
            return_value=httpx.Response(200, json=_fixture("asset.json")),
        )

        asset = client.get_asset("a2222222-2222-4222-8222-222222222222")

        assert asset.original_file_name == "IMG_0002.jpg"
        assert asset.duplicate_id == "dup-group-7"
        # step 20: manual star rating + reverse-geocoded place come off data
        # `sync` already fetches — no extra call.
        assert asset.rating == 4
        assert asset.exif_info is not None
        assert asset.exif_info.time_zone == "Europe/Podgorica"
        assert asset.exif_info.model == "iPhone 15 Pro"
        assert asset.exif_info.latitude == 42.44
        assert (asset.exif_info.city, asset.exif_info.state, asset.exif_info.country) == (
            "Lisbon",
            "Lisbon Municipality",
            "Portugal",
        )
        assert asset.exif_info.date_time_original is not None
        # ISO string with 'Z' parses to a timezone-aware instant.
        assert asset.exif_info.date_time_original.tzinfo is not None

    @respx.mock
    def test_get_duplicates(self, client: ImmichClient) -> None:
        respx.get(f"{_BASE_URL}/duplicates").mock(
            return_value=httpx.Response(200, json=_fixture("duplicates.json")),
        )

        groups = client.get_duplicates()

        assert len(groups) == 1
        assert groups[0].duplicate_id == "dup-group-7"
        assert [a.id for a in groups[0].assets] == [
            "a2222222-2222-4222-8222-222222222222",
            "a3333333-3333-4333-8333-333333333333",
        ]

    @respx.mock
    def test_get_faces(self, client: ImmichClient) -> None:
        route = respx.get(f"{_BASE_URL}/faces").mock(
            return_value=httpx.Response(200, json=_fixture("faces.json")),
        )

        faces = client.get_faces("a2222222-2222-4222-8222-222222222222")

        # `?id=` scopes the lookup to a single asset.
        assert route.calls[0].request.url.params["id"] == "a2222222-2222-4222-8222-222222222222"
        assert [f.id for f in faces] == ["face-1111", "face-2222", "face-3333", "face-4444"]
        # Assigned faces flatten person.id/name up to the boundary.
        assert (faces[0].person_id, faces[0].person_name) == (
            "p1111111-1111-4111-8111-111111111111",
            "Alex",
        )
        # A detected-but-unassigned face (person: null) is still a real face,
        # counted, but carries no person id/name.
        assert faces[2].person_id is None
        assert faces[2].person_name is None
        # All three sourceType variants parse (machine-learning | exif | manual).
        assert [f.source_type for f in faces] == [
            "machine-learning",
            "machine-learning",
            "exif",
            "manual",
        ]
        assert faces[3].person_name == "Sam"

    @respx.mock
    def test_upload_asset_posts_multipart_and_returns_id(
        self,
        client: ImmichClient,
        tmp_path: Path,
    ) -> None:
        ref = tmp_path / "leads.jpg"
        ref.write_bytes(b"\xff\xd8\xff-jpeg-bytes")
        route = respx.post(f"{_BASE_URL}/assets").mock(
            return_value=httpx.Response(201, json={"id": "ref-asset-1", "status": "created"}),
        )

        asset_id = client.upload_asset(ref)

        assert asset_id == "ref-asset-1"
        request = route.calls[0].request
        assert request.method == "POST"
        # Sent as multipart form-data (the file part), not JSON.
        assert request.headers["content-type"].startswith("multipart/form-data")

    @respx.mock
    def test_create_album_posts_name_and_assets_and_returns_id(self, client: ImmichClient) -> None:
        route = respx.post(f"{_BASE_URL}/albums").mock(
            return_value=httpx.Response(
                201,
                json={"id": "album-new-1", "albumName": "Trip 2026", "assetCount": 3},
            ),
        )

        album_id = client.create_album("Trip 2026", ["a1", "a2", "a3"])

        assert album_id == "album-new-1"
        request = route.calls[0].request
        assert request.method == "POST"
        body = json.loads(request.content)
        # `albumName` names the album; `assetIds` seeds its membership in one call.
        assert body["albumName"] == "Trip 2026"
        assert body["assetIds"] == ["a1", "a2", "a3"]

    @respx.mock
    def test_get_jobs_parses_per_queue_counts(self, client: ImmichClient) -> None:
        route = respx.get(f"{_BASE_URL}/jobs").mock(
            return_value=httpx.Response(200, json=_fixture("jobs.json")),
        )

        jobs = client.get_jobs()

        assert route.calls[0].request.method == "GET"
        # A busy queue (EXIF still extracting) reports active + waiting work.
        assert jobs["metadataExtraction"].active == 2
        assert jobs["metadataExtraction"].waiting == 5
        # An idle queue reports zero outstanding work — the readiness signal
        # the ingestion poll waits on.
        assert jobs["thumbnailGeneration"].active == 0
        assert jobs["thumbnailGeneration"].waiting == 0
        # Every queue Immich reports is surfaced.
        assert "faceDetection" in jobs

    @respx.mock
    def test_get_thumbnail_returns_bytes(self, client: ImmichClient) -> None:
        route = respx.get(f"{_BASE_URL}/assets/asset-1/thumbnail").mock(
            return_value=httpx.Response(200, content=b"\xff\xd8\xff-jpeg-bytes"),
        )

        data = client.get_thumbnail("asset-1")

        assert data == b"\xff\xd8\xff-jpeg-bytes"
        assert route.calls[0].request.url.params["size"] == "thumbnail"

    @respx.mock
    def test_get_original_returns_bytes(self, client: ImmichClient) -> None:
        route = respx.get(f"{_BASE_URL}/assets/asset-1/original").mock(
            return_value=httpx.Response(200, content=b"\xff\xd8\xff-full-res-jpeg-bytes"),
        )

        data = client.get_original("asset-1")

        assert data == b"\xff\xd8\xff-full-res-jpeg-bytes"
        assert route.calls[0].request.method == "GET"

    @respx.mock
    def test_sends_api_key_header(self, client: ImmichClient) -> None:
        route = respx.get(f"{_BASE_URL}/duplicates").mock(return_value=httpx.Response(200, json=[]))

        client.get_duplicates()

        assert route.calls[0].request.headers["x-api-key"] == "test-api-key"

    @respx.mock
    def test_raises_on_http_error(self, client: ImmichClient) -> None:
        respx.get(f"{_BASE_URL}/duplicates").mock(return_value=httpx.Response(401))

        with pytest.raises(httpx.HTTPStatusError):
            client.get_duplicates()

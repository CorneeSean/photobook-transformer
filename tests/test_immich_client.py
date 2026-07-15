"""Tests for :class:`~album.immich.client.ImmichClient` against recorded HTTP
fixtures (respx, not a live server).

Fixtures under tests/fixtures/immich/ are schema-accurate to the Immich OpenAPI
spec (v3.0.2); see knowledge/immich-api.md. They should be re-verified against a
live instance on the first real sync run.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from album.immich.client import ImmichClient

_FIXTURES = Path(__file__).parent / "fixtures" / "immich"
_BASE_URL = "http://immich.test/api"


def _fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text())


class TestImmichClient:
    """`ImmichClient`: pagination, parsing, auth header, and error propagation."""

    @pytest.fixture
    def client(self) -> ImmichClient:
        return ImmichClient(_BASE_URL, "test-api-key")

    @respx.mock
    def test_get_album_assets_follows_pagination(self, client: ImmichClient) -> None:
        route = respx.post(f"{_BASE_URL}/search/metadata").mock(
            side_effect=[
                httpx.Response(200, json=_fixture("search_metadata_page1.json")),
                httpx.Response(200, json=_fixture("search_metadata_page2.json")),
            ]
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
            return_value=httpx.Response(200, json=_fixture("asset.json"))
        )

        asset = client.get_asset("a2222222-2222-4222-8222-222222222222")

        assert asset.original_file_name == "IMG_0002.jpg"
        assert asset.duplicate_id == "dup-group-7"
        assert asset.exif_info is not None
        assert asset.exif_info.time_zone == "Europe/Podgorica"
        assert asset.exif_info.model == "iPhone 15 Pro"
        assert asset.exif_info.latitude == 42.44
        assert asset.exif_info.date_time_original is not None
        # ISO string with 'Z' parses to a timezone-aware instant.
        assert asset.exif_info.date_time_original.tzinfo is not None

    @respx.mock
    def test_get_duplicates(self, client: ImmichClient) -> None:
        respx.get(f"{_BASE_URL}/duplicates").mock(
            return_value=httpx.Response(200, json=_fixture("duplicates.json"))
        )

        groups = client.get_duplicates()

        assert len(groups) == 1
        assert groups[0].duplicate_id == "dup-group-7"
        assert [a.id for a in groups[0].assets] == [
            "a2222222-2222-4222-8222-222222222222",
            "a3333333-3333-4333-8333-333333333333",
        ]

    @respx.mock
    def test_get_thumbnail_returns_bytes(self, client: ImmichClient) -> None:
        route = respx.get(f"{_BASE_URL}/assets/asset-1/thumbnail").mock(
            return_value=httpx.Response(200, content=b"\xff\xd8\xff-jpeg-bytes")
        )

        data = client.get_thumbnail("asset-1")

        assert data == b"\xff\xd8\xff-jpeg-bytes"
        assert route.calls[0].request.url.params["size"] == "thumbnail"

    @respx.mock
    def test_get_original_returns_bytes(self, client: ImmichClient) -> None:
        route = respx.get(f"{_BASE_URL}/assets/asset-1/original").mock(
            return_value=httpx.Response(200, content=b"\xff\xd8\xff-full-res-jpeg-bytes")
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

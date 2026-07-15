"""Tests for the Lulu client against recorded HTTP fixtures (respx, not the live API).

Fixtures under tests/fixtures/lulu/ are schema-accurate to Lulu's public Print API
OpenAPI spec (https://api.lulu.com/api-docs/openapi-specs/openapi_public.yml, fetched
2026-07-14) — see the module docstring in src/album/lulu/client.py for what was
confirmed vs. assumed. They should be re-verified against a live sandbox instance on
the first real submission.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from album.lulu.client import LuluClient, PrintJobRequest, ShippingAddress

_FIXTURES = Path(__file__).parent / "fixtures" / "lulu"
_BASE_URL = "https://api.lulu.test"
_TOKEN_URL = f"{_BASE_URL}/auth/realms/glasstree/protocol/openid-connect/token"


def _fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text())


class TestLuluClient:
    """`LuluClient`: OAuth token flow, request body, token caching, errors."""

    @pytest.fixture
    def client(self) -> LuluClient:
        return LuluClient(_BASE_URL, "test-client-key", "test-client-secret")

    def _mock_token(self) -> respx.Route:
        return respx.post(_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=_fixture("token.json"))
        )

    def _request(self) -> PrintJobRequest:
        return PrintJobRequest(
            contact_email="orders@example.com",
            external_id="honeymoon-2026",
            shipping_level="MAIL",
            line_items=[
                {
                    "quantity": 1,
                    "pod_package_id": "0850X0850FCPRECW060UW444MXX",
                    "title": "Our Honeymoon",
                    "external_id": "honeymoon-2026",
                    "interior": {
                        "source_url": "https://example.com/albums/honeymoon-2026/book.pdf"
                    },
                    "cover": {"source_url": "https://example.com/albums/honeymoon-2026/book.pdf"},
                }
            ],
            shipping_address=ShippingAddress(
                name="Alex Morgan",
                street1="1 Main St",
                city="Springfield",
                postcode="00000",
                country_code="US",
                phone_number="+15555555555",
            ),
        )

    @respx.mock
    def test_authenticates_before_submitting(self, client: LuluClient) -> None:
        token_route = self._mock_token()
        job_route = respx.post(f"{_BASE_URL}/print-jobs/").mock(
            return_value=httpx.Response(201, json=_fixture("print_job_created.json"))
        )

        result = client.submit_print_job(self._request())

        assert token_route.call_count == 1
        assert result.id == 42424
        assert result.status.name == "CREATED"
        # Bearer token from the token response is used on the print-job call.
        assert (
            job_route.calls[0].request.headers["Authorization"] == "Bearer test-access-token-abc123"
        )

    @respx.mock
    def test_submit_print_job_sends_expected_body(self, client: LuluClient) -> None:
        self._mock_token()
        job_route = respx.post(f"{_BASE_URL}/print-jobs/").mock(
            return_value=httpx.Response(201, json=_fixture("print_job_created.json"))
        )

        client.submit_print_job(self._request())

        body = json.loads(job_route.calls[0].request.content)
        assert body["contact_email"] == "orders@example.com"
        assert body["shipping_level"] == "MAIL"
        assert body["line_items"][0]["pod_package_id"] == "0850X0850FCPRECW060UW444MXX"
        assert body["shipping_address"]["country_code"] == "US"

    @respx.mock
    def test_reuses_token_across_calls(self, client: LuluClient) -> None:
        token_route = self._mock_token()
        respx.post(f"{_BASE_URL}/print-jobs/").mock(
            return_value=httpx.Response(201, json=_fixture("print_job_created.json"))
        )

        client.submit_print_job(self._request())
        client.submit_print_job(self._request())

        # One token fetch, two print-job submissions — the access token is cached.
        assert token_route.call_count == 1

    @respx.mock
    def test_get_print_job_status(self, client: LuluClient) -> None:
        self._mock_token()
        respx.get(f"{_BASE_URL}/print-jobs/42424/").mock(
            return_value=httpx.Response(200, json=_fixture("print_job_status_shipped.json"))
        )

        status = client.get_print_job(42424)

        assert status.id == 42424
        assert status.status.name == "SHIPPED"

    @respx.mock
    def test_raises_on_validation_error(self, client: LuluClient) -> None:
        self._mock_token()
        respx.post(f"{_BASE_URL}/print-jobs/").mock(
            return_value=httpx.Response(400, json=_fixture("error_validation.json"))
        )

        with pytest.raises(httpx.HTTPStatusError):
            client.submit_print_job(self._request())

    @respx.mock
    def test_raises_on_auth_failure(self, client: LuluClient) -> None:
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(401))

        with pytest.raises(httpx.HTTPStatusError):
            client.submit_print_job(self._request())

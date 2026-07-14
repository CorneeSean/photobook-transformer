"""A thin, typed wrapper over Lulu's Print API.

This is the *only* module that talks to Lulu over HTTP — it makes no fulfillment
decisions (what product, what recipient, what shipping level) of its own. The
[`LuluPrinter`](../printers/lulu.py) (an opt-in
[`Printer`](../printers/base.py) the `fulfill` stage can delegate to) owns that
policy — which SKU, which recipient, submitting an album's assembled PDF and
returning the order id — and calls into this module only for the HTTP exchange,
exactly like [`immich/client.py`](../immich/client.py) never decides what to sync.

Modelled against Lulu's public Print API OpenAPI spec
(https://api.lulu.com/api-docs/openapi-specs/openapi_public.yml, fetched 2026-07-14).
Confirmed directly from that spec:

- **Auth is OAuth2 client-credentials**, via Keycloak-style
  ``POST /auth/realms/glasstree/protocol/openid-connect/token`` with an HTTP Basic
  ``client_key:client_secret`` header and ``grant_type=client_credentials`` form body,
  returning a bearer ``access_token`` (+ ``expires_in``) used as
  ``Authorization: Bearer <token>`` on subsequent calls.
- **Print job creation** is ``POST /print-jobs/`` with ``line_items`` (each carrying
  ``pod_package_id``, ``quantity``, and either a ``printable_id`` or ``interior`` +
  ``cover`` source URLs), a ``shipping_address``, ``contact_email``, and
  ``shipping_level``. The response echoes the job with an integer ``id`` and a
  ``status: {name, message}`` object; ``status.name`` is one of ``CREATED``,
  ``UNPAID``, ``PAYMENT_IN_PROGRESS``, ``PRODUCTION_DELAYED``, ``PRODUCTION_READY``,
  ``IN_PRODUCTION``, ``SHIPPED``, ``REJECTED``, ``CANCELED`` (``ERROR`` also appears
  on the status endpoint).
- Order status updates arrive via a **``PRINT_JOB_STATUS_CHANGED`` webhook**
  (``name``/``message``/``changed``/``line_item_statuses``/``print_job_id``,
  HMAC-signed over the raw body) — *receiving* that webhook is out of scope for this
  step (build step 15 only covers submission); :meth:`LuluClient.get_print_job`
  (``GET /print-jobs/{id}/``) is provided for polling as a synchronous alternative.

**Assumed, not directly confirmed** (the fetched spec excerpt didn't show this
endpoint's full schema): the exact response shape of ``GET /print-jobs/{id}/`` is
inferred from the list endpoint and the webhook payload's ``line_item_statuses``
shape — both point at the same ``status`` object, but this should be re-verified
against a live sandbox call before the first real submission (see the deferred
manual verification note in fulfill.md / progress.md).
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict

from album.config import get_settings

_TOKEN_PATH = "/auth/realms/glasstree/protocol/openid-connect/token"

# Refresh the cached token this many seconds before its reported expiry, so a
# request doesn't race a token that's about to lapse mid-flight.
_TOKEN_REFRESH_MARGIN_S = 60


class ShippingAddress(BaseModel):
    """A Lulu ``shipping_address`` object. Only the fields this pipeline needs."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    street1: str
    street2: str | None = None
    city: str
    state_code: str | None = None
    postcode: str
    country_code: str
    phone_number: str
    email: str | None = None


class PrintJobRequest(BaseModel):
    """Request body for ``POST /print-jobs/``."""

    model_config = ConfigDict(populate_by_name=True)

    contact_email: str
    external_id: str | None = None
    shipping_level: Literal["MAIL", "PRIORITY_MAIL", "GROUND", "EXPEDITED", "EXPRESS"]
    line_items: list[dict[str, Any]]
    shipping_address: ShippingAddress


class PrintJobStatus(BaseModel):
    """The ``status`` object on a print job."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    message: str | None = None
    changed: str | None = None


class PrintJob(BaseModel):
    """The subset of Lulu's print-job response this pipeline consumes."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    status: PrintJobStatus
    contact_email: str | None = None
    external_id: str | None = None
    shipping_level: str | None = None


class _TokenResponse(BaseModel):
    access_token: str
    expires_in: int
    token_type: str


class LuluClient:
    """Thin typed wrapper over Lulu's Print API.

    Construct with an explicit ``base_url`` + client credentials (tests do this
    against ``respx``), or via :meth:`from_settings` for real runs. Usable as a
    context manager so the underlying HTTP connection pool is closed.

    Handles OAuth2 client-credentials auth transparently: the access token is
    fetched lazily on first use and cached until it's close to expiry.
    """

    def __init__(
        self,
        base_url: str,
        client_key: str,
        client_secret: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._client_key = client_key
        self._client_secret = client_secret
        self._client = http_client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    @classmethod
    def from_settings(cls) -> LuluClient:
        settings = get_settings()
        return cls(settings.lulu_base_url, settings.lulu_client_key, settings.lulu_client_secret)

    def submit_print_job(self, request: PrintJobRequest) -> PrintJob:
        """Create a print job via ``POST /print-jobs/``."""
        response = self._client.post(
            "/print-jobs/",
            json=request.model_dump(mode="json", exclude_none=True),
            headers=self._auth_header(),
        )
        response.raise_for_status()
        return PrintJob.model_validate(response.json())

    def get_print_job(self, print_job_id: int) -> PrintJob:
        """Fetch current status via ``GET /print-jobs/{id}/``."""
        response = self._client.get(f"/print-jobs/{print_job_id}/", headers=self._auth_header())
        response.raise_for_status()
        return PrintJob.model_validate(response.json())

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _access_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        response = self._client.post(
            _TOKEN_PATH,
            data={"grant_type": "client_credentials"},
            auth=(self._client_key, self._client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token = _TokenResponse.model_validate(response.json())
        self._token = token.access_token
        self._token_expires_at = time.monotonic() + token.expires_in - _TOKEN_REFRESH_MARGIN_S
        return self._token

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

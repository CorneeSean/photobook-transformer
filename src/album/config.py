"""The only module in this app that reads environment variables.

Every other module that needs configuration gets it from :func:`get_settings`.
"""

from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # `__` is the nested-group separator (e.g. a future `LULU__CLIENT_KEY` would
    # populate a `lulu.client_key` field) — configured now so grouped settings
    # added in later steps don't need this file touched again.
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    database_url: str

    # Immich REST API (used by src/album/immich/client.py from step 4 onward).
    # Defaults keep DB-only flows (alembic, migrate) working without Immich vars
    # set; a real sync run needs IMMICH_API_KEY filled in from Immich's web UI.
    immich_base_url: str = "http://localhost:2283/api"
    immich_api_key: str = ""

    # Claude, via PydanticAI (used by src/album/agents/analysis.py from step 9
    # onward). Defaulted so importing the agent module never requires a key —
    # only building the real (non-test) model does.
    anthropic_api_key: str = ""

    # Lulu Print API (used by src/album/lulu/client.py from step 15 onward).
    # OAuth2 client-credentials — see lulu/client.py's module docstring. Defaulted
    # empty so importing the client never requires credentials; only building the
    # real (non-test) client and submitting a real print job needs them filled in.
    lulu_base_url: str = "https://api.lulu.com"
    lulu_client_key: str = ""
    lulu_client_secret: str = ""

    # Order-level fields Lulu requires that aren't part of the Album/Photo data
    # model (they describe the *recipient*, not the album) — placeholders until
    # a real print run needs them. `lulu_pod_package_id` selects Lulu's physical
    # product (trim size / paper / binding); pin down the real SKU alongside open
    # question #4 (trim/bleed spec) before the first real submission.
    lulu_contact_email: str = ""
    lulu_pod_package_id: str = ""
    lulu_shipping_name: str = ""
    lulu_shipping_street1: str = ""
    lulu_shipping_city: str = ""
    lulu_shipping_postcode: str = ""
    lulu_shipping_country_code: str = ""
    lulu_shipping_phone_number: str = ""


@cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue] -- fields come from the environment

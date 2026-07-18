"""The only module in this app that reads environment variables.

Every other module that needs configuration gets it from :func:`get_settings`.

Settings are grouped into domain sub-models (:class:`ImmichSettings`,
:class:`LuluSettings`) reached as ``settings.immich`` / ``settings.lulu``. The
``__`` nested delimiter maps each nested field to a ``GROUP__FIELD`` env var —
``IMMICH__API_KEY`` populates ``settings.immich.api_key``,
``LULU__SHIPPING_CITY`` populates ``settings.lulu.shipping_city``.

Two fields stay flat on purpose:

- ``database_url`` — a single value AND a de-facto standard env name
  (``DATABASE_URL``) consumed by ``alembic/env.py`` and ``docker-compose.yml``;
  nesting it to ``DATABASE__URL`` would break those.
- ``anthropic_api_key`` — a single field whose conventional env name is
  ``ANTHROPIC_API_KEY``. Nesting a one-field group buys no grouping, so it stays
  flat like ``database_url``.
"""

from functools import cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ImmichSettings(BaseModel):
    """Immich REST API config (used by src/album/immich/client.py).

    Env: ``IMMICH__BASE_URL``, ``IMMICH__API_KEY``. Defaults keep DB-only flows
    (alembic, migrate) working without Immich vars set; a real sync run needs
    ``IMMICH__API_KEY`` filled in from Immich's web UI.
    """

    base_url: str = "http://localhost:2283/api"
    api_key: str = ""


class LuluSettings(BaseModel):
    """Lulu Print API config (used by src/album/lulu/client.py and
    src/album/printers/lulu.py).

    Env: ``LULU__CLIENT_KEY``, ``LULU__CLIENT_SECRET``, ``LULU__SHIPPING_CITY``,
    etc. OAuth2 client-credentials — see lulu/client.py's module docstring.
    Defaulted empty so importing the client never requires credentials; only
    building the real (non-test) client and submitting a real print job needs
    them filled in.

    The shipping / order-level fields describe the *recipient*, not the album,
    so they aren't part of the Album/Photo data model. ``pod_package_id``
    selects Lulu's physical product (trim size / paper / binding); pin down the
    real SKU alongside open question #4 (trim/bleed spec) before the first real
    submission.
    """

    base_url: str = "https://api.lulu.com"
    client_key: str = ""
    client_secret: str = ""
    contact_email: str = ""
    pod_package_id: str = ""
    shipping_name: str = ""
    shipping_street1: str = ""
    shipping_city: str = ""
    shipping_postcode: str = ""
    shipping_country_code: str = ""
    shipping_phone_number: str = ""


class QualitySettings(BaseModel):
    """`quality` stage's aesthetic scorer / CLIP embedder config (build step 31).

    Env: ``QUALITY__SCORER``, ``QUALITY__DEVICE``, etc. Drives
    :func:`album.flow._flow._build_quality_model`, which turns ``scorer`` into a
    concrete model — the string→model mapping and the ``device``/``precision``
    validation live in that builder, **not here**, for the same import-cycle reason
    the ``captioner`` registry is kept out of this module (config must not import the
    heavy ``analysis`` adapters).

    ``scorer`` defaults to ``"clip"`` (the real ViT-L/14 CLIP + LAION head): the
    scorer is local and is the core curation signal, so it's on wherever the
    capability exists. The builder auto-degrades to the NULL-scoring placeholder
    (with a loud warning) when the ``scoring`` extra or the weights are absent, so a
    base install still runs — set ``scorer="none"`` to opt out explicitly.
    """

    scorer: str = "clip"  # "clip" | "none"; validated in the builder, not here
    device: str = "cpu"  # "cpu" | "mps" (Apple Silicon) | "cuda"; validated in the builder
    precision: str = "fp32"  # "fp32" | "fp16"; builder rejects fp16 on cpu (MPS/CUDA only)
    # "-quickgelu", not plain "ViT-L-14": OpenAI's CLIP weights were trained with the
    # QuickGELU activation, and the LAION aesthetic head was trained on those QuickGELU
    # embeddings, so pairing plain-GELU "ViT-L-14" with pretrained="openai" feeds the
    # head subtly wrong features (open_clip warns about exactly this mismatch).
    clip_model: str = "ViT-L-14-quickgelu"
    clip_pretrained: str = "openai"
    weights_dir: str = ".cache/aesthetic"  # open_clip + LAION head cache
    aesthetic_weights: str = "sac+logos+ava1-l14-linearMSE.pth"


class Settings(BaseSettings):
    # `__` is the nested-group separator: `IMMICH__API_KEY` populates
    # `immich.api_key`, `LULU__SHIPPING_CITY` populates `lulu.shipping_city`.
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    database_url: str

    # Claude, via PydanticAI (used by album/analysis/captioner.py's ClaudeCaptioner
    # and album/agents/segments.py). Kept flat under its conventional env name;
    # defaulted so importing those modules never requires a key — only building the
    # real (non-test) model does.
    anthropic_api_key: str = ""

    # The default captioner for a run (step 29): "template" (offline, no API key,
    # deterministic) or "claude" (opt-in rich caption). Flat like the two above (a
    # single value, conventional env name ``CAPTIONER``); a ``--captioner`` CLI flag
    # overrides it per run, and ``build_captioner`` validates the value — kept out of
    # this module to avoid importing the captioner registry (an import cycle).
    captioner: str = "template"

    immich: ImmichSettings = ImmichSettings()
    lulu: LuluSettings = LuluSettings()
    quality: QualitySettings = QualitySettings()


@cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue] -- fields come from the environment

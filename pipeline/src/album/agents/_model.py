"""The shared default Claude model for the PydanticAI agents.

Both [`analysis.py`](./analysis.py) and [`segments.py`](./segments.py) run against
the same model, so its construction lives here once rather than being copied per
agent. Cached because each ``AnthropicProvider`` owns its own httpx client /
connection pool, and a real run calls the agents many times — rebuilding per call
would leak an unclosed client and a fresh TLS handshake each time. Tests inject
``TestModel``/``FunctionModel`` and never reach this.
"""

from __future__ import annotations

from functools import cache

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from album.config import get_settings

_DEFAULT_MODEL_NAME = "claude-sonnet-5"


@cache
def default_model() -> Model:
    """The real Claude model built from ``config.py``'s ``anthropic_api_key``."""
    provider = AnthropicProvider(api_key=get_settings().anthropic_api_key)
    return AnthropicModel(_DEFAULT_MODEL_NAME, provider=provider)

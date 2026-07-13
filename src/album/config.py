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


@cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue] -- fields come from the environment

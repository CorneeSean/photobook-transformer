from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from album.config import get_settings


class TestConfig:
    """`get_settings`: reads DATABASE_URL from the environment, cached."""

    @pytest.fixture(autouse=True)
    def _clear_settings_cache(self) -> Iterator[None]:
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_reads_database_url_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host:5432/db")

        settings = get_settings()

        assert settings.database_url == "postgresql+psycopg://u:p@host:5432/db"

    def test_get_settings_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host:5432/db")

        assert get_settings() is get_settings()

    def test_missing_database_url_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Run outside the repo (no .env to fall back to) so DATABASE_URL is genuinely absent.
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(ValidationError, match="database_url"):
            get_settings()


class TestNestedGroups:
    """The `__`-delimited nested sub-models (`immich`, `lulu`) bind from env,
    fall back to defaults, and leave the flat fields flat."""

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
        # Isolate from the repo `.env` (chdir away) and pin the one required
        # flat field so nested resolution is what's under test.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host:5432/db")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_immich_group_binds_from_double_underscore_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("IMMICH__BASE_URL", "http://immich.example/api")
        monkeypatch.setenv("IMMICH__API_KEY", "immich-secret")

        immich = get_settings().immich

        assert immich.base_url == "http://immich.example/api"
        assert immich.api_key == "immich-secret"

    def test_lulu_group_binds_from_double_underscore_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LULU__CLIENT_KEY", "ck")
        monkeypatch.setenv("LULU__CLIENT_SECRET", "cs")
        monkeypatch.setenv("LULU__SHIPPING_CITY", "Reykjavik")

        lulu = get_settings().lulu

        assert lulu.client_key == "ck"
        assert lulu.client_secret == "cs"
        assert lulu.shipping_city == "Reykjavik"

    def test_groups_fall_back_to_defaults_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("IMMICH__BASE_URL", "IMMICH__API_KEY", "LULU__CLIENT_KEY"):
            monkeypatch.delenv(var, raising=False)

        settings = get_settings()

        assert settings.immich.base_url == "http://localhost:2283/api"
        assert settings.immich.api_key == ""
        assert settings.lulu.base_url == "https://api.lulu.com"
        assert settings.lulu.client_key == ""

    def test_flat_fields_stay_flat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # database_url and anthropic_api_key are read from their bare env names,
        # NOT DATABASE__URL / ANTHROPIC__API_KEY.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")

        settings = get_settings()

        assert settings.database_url == "postgresql+psycopg://u:p@host:5432/db"
        assert settings.anthropic_api_key == "anthropic-secret"

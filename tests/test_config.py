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
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Run outside the repo (no .env to fall back to) so DATABASE_URL is genuinely absent.
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(ValidationError, match="database_url"):
            get_settings()

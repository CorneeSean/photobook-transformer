"""Shared helpers for the pgvector container tests.

Used by ``test_similarity.py`` (the raw cosine query), ``test_pgvector_integration.py``
(the migrated end-to-end suite), and ``test_flow_integration.py`` (the flow's
concurrent-per-task-session test) so the vector builder, the container image,
and the alembic-migration plumbing are defined in exactly one place
(quality-practices.md §2.4 — a helper shared across files lives in one module).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from alembic.config import Config
from alembic.script import ScriptDirectory

from album.config import get_settings

# Pinned pgvector image for every container-backed test.
PGVECTOR_IMAGE = "pgvector/pgvector:pg17"

# The pipeline's CLIP embedding width (data-model.md's Vector(768)).
_DIM = 768


def vector(*nonzero: tuple[int, float]) -> list[float]:
    """A 768-dim embedding that is zero except at the given ``(index, value)``
    positions — lets a test set just the couple of components that make the cosine
    similarity easy to reason about."""
    v = [0.0] * _DIM
    for index, value in nonzero:
        v[index] = value
    return v


@contextmanager
def database_url(url: str) -> Iterator[None]:
    """Point ``config.py`` (and thus alembic's ``env.py``) at ``url`` transiently.

    ``env.py`` overrides alembic.ini's ``sqlalchemy.url`` with
    ``get_settings().database_url`` (config.py is the only env-var reader), so the
    only way to aim a migration at a container is via ``DATABASE_URL`` — set it,
    clear the ``@cache``, and restore both afterwards so no other test is affected.
    """
    original = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original
        get_settings.cache_clear()


def alembic_config() -> Config:
    """Alembic config built programmatically — deliberately *without* the .ini path.

    ``env.py`` only calls ``logging.config.fileConfig`` when ``config_file_name``
    is set; that call has ``disable_existing_loggers=True``, which would silence
    the app loggers and break ``caplog`` assertions in *other* test files that run
    after a migration here. Omitting the .ini skips it; ``script_location`` and the
    DB url (from ``get_settings`` via :func:`database_url`) are all env.py needs.
    """
    config = Config()
    config.set_main_option("script_location", "alembic")
    return config


def alembic_head() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head() or ""

"""``flow`` — Prefect orchestration package for one album, start to finish.

The public surface (the ``@flow`` entry point and the album loader) lives in
:mod:`album.flow._flow`; this package re-exports it so callers keep importing
``from album.flow import pipeline`` / ``get_or_create_album``.
"""

from album.flow._flow import get_or_create_album, pipeline

__all__ = ["get_or_create_album", "pipeline"]

"""``flow`` — Prefect orchestration package for one album, start to finish.

The public surface (the ``@flow`` entry point, the :class:`~album.flow._flow.Pipeline`
wiring class it's built from, and the album loader) lives in :mod:`album.flow._flow`;
this package re-exports it so callers keep importing ``from album.flow import
pipeline`` / ``get_or_create_album``.
"""

from album.flow._flow import Pipeline, get_or_create_album, pipeline

__all__ = ["Pipeline", "get_or_create_album", "pipeline"]

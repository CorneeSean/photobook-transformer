"""Host↔container path translation for external-library ingestion.

In ``external`` ingest mode (knowledge/build/external-library-ingestion.md) the
photo directory is bind-mounted read-only into the Immich server, and Immich
scans it *in place* rather than storing a copy. The CLI and manifest speak **host**
paths (``/home/devel/photos/lisbon/img_1.jpg``); Immich reports each scanned asset's
**container** path in ``AssetResponseDto.originalPath`` (``/external/lisbon/img_1.jpg``).
:class:`PathMapping` converts between the two so the registrar (build step 37) can
tie each host photo to its scanned asset id.

This module is pure path arithmetic — no filesystem or network. Callers pass
already-resolved host paths (the ingestor collects images under
``directory.resolve()``); container paths are Immich's own strings and are never
resolved on the host (``/external`` need not exist here).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class PathMapping:
    """A single bind mount: ``host_root`` on the host is ``container_root`` inside
    the Immich container. Both are the *roots* of the mounted tree, not individual
    files.

    ``container_root`` is a POSIX path (the Immich server is Linux) regardless of
    the host OS, so it is kept as a :class:`~pathlib.PurePosixPath` — on a macOS
    or Windows host a plain ``Path`` would mangle ``/external``.
    """

    host_root: Path
    container_root: PurePosixPath

    def to_container(self, host_path: Path) -> PurePosixPath:
        """The container path Immich reports for ``host_path``.

        Raises :class:`ValueError` — the likeliest external-mode misconfiguration —
        when ``host_path`` lies outside ``host_root`` (so it can't be scanned).
        """
        if not host_path.is_relative_to(self.host_root):
            raise ValueError(
                f"{host_path} is not under the external-library root {self.host_root}; "
                "set IMMICH__EXTERNAL_HOST_ROOT to a directory that contains the photos",
            )
        relative = host_path.relative_to(self.host_root)
        return self.container_root.joinpath(*relative.parts)

    def to_host(self, container_path: PurePosixPath) -> Path:
        """The host path for a container ``originalPath`` — the inverse of
        :meth:`to_container`. Raises :class:`ValueError` if it is outside
        ``container_root``."""
        if not container_path.is_relative_to(self.container_root):
            raise ValueError(
                f"{container_path} is not under the container root {self.container_root}",
            )
        relative = container_path.relative_to(self.container_root)
        return self.host_root.joinpath(*relative.parts)

    @classmethod
    def from_settings(cls, host_root: str, container_root: str) -> PathMapping:
        """Build from the raw ``IMMICH__EXTERNAL_*_ROOT`` config strings, resolving
        the host root so it matches the resolved photo paths the ingestor collects."""
        return cls(Path(host_root).resolve(), PurePosixPath(container_root))

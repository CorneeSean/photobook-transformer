"""Small, dependency-free helpers for turning a folder name into an album's
slug / title. Hoisted out of :mod:`album.ingest` so :mod:`album.manifest` can
reuse them without an ``ingest ↔ manifest`` import cycle (manifest computes an
album's effective slug to detect collisions; ingest defaults slug/title from a
directory name). They speak only "name → slug / title", so the
[genericity rule](../../knowledge/reference/folder-structure.md) is unaffected.
"""

from __future__ import annotations

import re


def slugify(name: str) -> str:
    """A filesystem/URL-safe album slug from a directory name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "album"


def titleize(name: str) -> str:
    """A human title from a directory name: ``my-photos_2026`` → ``My Photos 2026``."""
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(word.capitalize() for word in words if word)

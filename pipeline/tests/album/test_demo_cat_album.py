"""Smoke test for the offline demo ``scripts/build_cat_album.py``.

``scripts/`` sits outside the basedpyright/xenon gate and isn't an importable
package, so the demo has rotted silently before — first the ``flow/`` Stage move,
then the ``Stage(album_id, session_factory).run()`` API change each broke its
calls undetected (see [log.md](../../knowledge/log.md)). This drives the demo end
to end over the committed ``tests/data/cat-album`` fixture and asserts a real,
non-empty book is produced, so ``poe check`` catches the next break.

The demo is loaded by file path (it lives outside the ``src``/``tests`` import
roots); ``build_cat_album`` is parametrized on ``output_dir`` so the book lands in
``tmp_path`` rather than beside the committed fixture.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_cat_album.py"


def _load_demo() -> Any:
    """Load ``scripts/build_cat_album.py`` as a module from its file path."""
    spec = importlib.util.spec_from_file_location("cat_album_demo", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_builds_a_browsable_book(tmp_path: Path) -> None:
    demo = _load_demo()

    index_path = demo.build_cat_album(output_dir=tmp_path)

    # HtmlPrinter writes <output_dir>/<slug>/book/index.html and returns its path.
    assert index_path == tmp_path / "cat-album" / "book" / "index.html"
    assert index_path.is_file()

    html = index_path.read_text(encoding="utf-8")
    # A real, complete book: a fixture segment label rendered as a section header
    # proves the whole cluster -> assemble -> print chain ran (a header needs
    # assemble's pages, which need cluster's timestamp-derived scenes), and all 30
    # fixture photos were placed.
    assert "Dawn Patrol" in html
    assert html.count("<img") == 30

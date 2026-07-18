"""Tests for :mod:`album.ingest` — a photo directory → an Immich album + a
materialized ``album.yml``, over a fake Immich client and a temp directory.

The Immich HTTP layer is stubbed by a fake :class:`~album.ingest.IngestClient`;
``sleep`` is injected as a no-op so the readiness poll never actually waits. What
the ingestor writes is asserted both structurally and by round-tripping it back
through the real loader (``AlbumDefinition``), proving the generated file is a
valid ``album.yml``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from album.definition import AlbumDefinition, LeadFacesDefinition
from album.immich.client import ImmichAsset, ImmichJobCounts
from album.ingest import DirectoryIngestor, IngestError, patch_lead_faces


def _busy(active: int = 0, waiting: int = 0) -> dict[str, ImmichJobCounts]:
    """A ``get_jobs`` response with the EXIF queue carrying the given work."""
    return {"metadataExtraction": ImmichJobCounts(active=active, waiting=waiting)}


class FakeIngestClient:
    """In-memory :class:`~album.ingest.IngestClient` for tests.

    ``jobs`` is a *sequence* of successive ``get_jobs`` responses (to model
    Immich's async processing, e.g. ``[busy, busy, idle]``); the last entry
    repeats once exhausted. Default: an empty map → idle immediately. ``assets``
    is what ``get_album_assets`` returns (the segments-agent capture-range anchor).
    """

    def __init__(
        self,
        *,
        jobs: list[dict[str, ImmichJobCounts]] | None = None,
        assets: list[ImmichAsset] | None = None,
    ) -> None:
        self._jobs = list(jobs) if jobs is not None else None
        self._assets = assets or []
        self.uploaded: list[Path] = []
        self.created: list[tuple[str, list[str]]] = []
        self.jobs_calls = 0

    def upload_asset(self, path: Path) -> str:
        self.uploaded.append(path)
        return f"asset-{len(self.uploaded)}"

    def create_album(self, name: str, asset_ids: list[str]) -> str:
        self.created.append((name, list(asset_ids)))
        return "immich-new-1"

    def get_jobs(self) -> dict[str, ImmichJobCounts]:
        self.jobs_calls += 1
        if self._jobs is None:
            return {}
        return self._jobs.pop(0) if len(self._jobs) > 1 else self._jobs[0]

    def get_album_assets(self, immich_album_id: str) -> list[ImmichAsset]:
        return self._assets


class TestDirectoryIngest:
    """`DirectoryIngestor.ingest`: walk → upload → create album → materialize."""

    def _ingestor(
        self,
        client: FakeIngestClient,
        albums_dir: Path,
        **kw: object,
    ) -> DirectoryIngestor:
        # No-op sleep + tiny timeout so the readiness poll is fast and silent.
        return DirectoryIngestor(
            client,
            albums_dir=albums_dir,
            poll_interval_s=1.0,
            poll_timeout_s=3.0,
            sleep=lambda _s: None,
            **kw,  # pyright: ignore[reportArgumentType]
        )

    def _photos_dir(self, tmp_path: Path, name: str, files: list[str]) -> Path:
        directory = tmp_path / name
        directory.mkdir()
        for filename in files:
            (directory / filename).write_bytes(b"\xff\xd8\xff-bytes")
        return directory

    def test_uploads_all_images_creates_album_and_writes_valid_yaml(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path, "lisbon-trip", ["a.jpg", "b.png", "c.heic"])
        client = FakeIngestClient()
        albums_dir = tmp_path / "albums"

        slug = self._ingestor(client, albums_dir).ingest(directory)

        assert slug == "lisbon-trip"
        # Every image uploaded (sorted), then the album created from those ids.
        assert [p.name for p in client.uploaded] == ["a.jpg", "b.png", "c.heic"]
        assert client.created == [("Lisbon Trip", ["asset-1", "asset-2", "asset-3"])]
        # A minimal album.yml was materialized and round-trips through the loader.
        album_yml = albums_dir / "lisbon-trip" / "album.yml"
        written = yaml.safe_load(album_yml.read_text())
        assert written == {
            "slug": "lisbon-trip",
            "title": "Lisbon Trip",
            "immich_album_id": "immich-new-1",
        }
        loaded = AlbumDefinition().load(album_yml)
        assert (loaded.slug, loaded.title, loaded.immich_album_id) == (
            "lisbon-trip",
            "Lisbon Trip",
            "immich-new-1",
        )

    def test_recurses_into_subdirectories(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path, "trip", ["top.jpg"])
        nested = directory / "day-1"
        nested.mkdir()
        (nested / "deep.jpg").write_bytes(b"\xff\xd8\xff")
        client = FakeIngestClient()

        self._ingestor(client, tmp_path / "albums").ingest(directory)

        assert {p.name for p in client.uploaded} == {"top.jpg", "deep.jpg"}

    def test_non_image_files_are_ignored(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path, "trip", ["keep.jpg", "notes.txt", "clip.mov"])
        client = FakeIngestClient()

        self._ingestor(client, tmp_path / "albums").ingest(directory)

        assert [p.name for p in client.uploaded] == ["keep.jpg"]

    def test_empty_directory_raises(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path, "empty", [])
        client = FakeIngestClient()

        with pytest.raises(IngestError, match="no images found"):
            self._ingestor(client, tmp_path / "albums").ingest(directory)
        assert client.created == []  # never created an album for nothing

    @pytest.mark.parametrize(
        ("dir_name", "expected_slug", "expected_title"),
        [
            ("lisbon-trip", "lisbon-trip", "Lisbon Trip"),
            ("Trip 2026", "trip-2026", "Trip 2026"),
            ("Sam_&_Alex", "sam-alex", "Sam & Alex"),
        ],
        ids=["kebab", "spaced", "punctuation"],
    )
    def test_slug_and_title_default_from_directory_name(
        self,
        tmp_path: Path,
        dir_name: str,
        expected_slug: str,
        expected_title: str,
    ) -> None:
        directory = self._photos_dir(tmp_path, dir_name, ["a.jpg"])
        client = FakeIngestClient()

        slug = self._ingestor(client, tmp_path / "albums").ingest(directory)

        assert slug == expected_slug
        assert client.created[0][0] == expected_title

    def test_explicit_slug_and_title_win(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path, "raw-dump", ["a.jpg"])
        client = FakeIngestClient()
        albums_dir = tmp_path / "albums"

        slug = self._ingestor(client, albums_dir).ingest(
            directory,
            slug="honeymoon-2026",
            title="Our Honeymoon",
        )

        assert slug == "honeymoon-2026"
        assert client.created[0][0] == "Our Honeymoon"
        written = yaml.safe_load((albums_dir / "honeymoon-2026" / "album.yml").read_text())
        assert written["slug"] == "honeymoon-2026"
        assert written["title"] == "Our Honeymoon"

    def test_timezone_written_when_given(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path, "trip", ["a.jpg"])
        albums_dir = tmp_path / "albums"

        self._ingestor(FakeIngestClient(), albums_dir).ingest(directory, timezone="Europe/Warsaw")

        written = yaml.safe_load((albums_dir / "trip" / "album.yml").read_text())
        assert written["timezone"] == "Europe/Warsaw"

    def test_timezone_omitted_when_absent(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path, "trip", ["a.jpg"])
        albums_dir = tmp_path / "albums"

        self._ingestor(FakeIngestClient(), albums_dir).ingest(directory)

        assert "timezone" not in yaml.safe_load((albums_dir / "trip" / "album.yml").read_text())

    def test_lead_face_images_written_as_absolute_reference_images(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path, "trip", ["a.jpg"])
        albums_dir = tmp_path / "albums"
        alex = tmp_path / "alex.jpg"
        alex.write_bytes(b"\xff\xd8\xff")
        sam = tmp_path / "sam.jpg"
        sam.write_bytes(b"\xff\xd8\xff")

        self._ingestor(FakeIngestClient(), albums_dir).ingest(
            directory,
            lead_face_images=[alex, sam],
        )

        album_yml = albums_dir / "trip" / "album.yml"
        # Absolute paths so they resolve regardless of album.yml's directory.
        lead = LeadFacesDefinition().load(album_yml)
        assert lead is not None
        assert lead.reference_images == [str(alex.resolve()), str(sam.resolve())]

    def test_lead_faces_omitted_when_absent(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path, "trip", ["a.jpg"])
        albums_dir = tmp_path / "albums"

        self._ingestor(FakeIngestClient(), albums_dir).ingest(directory)

        assert "lead_faces" not in yaml.safe_load((albums_dir / "trip" / "album.yml").read_text())


class TestReRunGuard:
    """The load-once re-run guard: refuse an existing slug unless ``force``."""

    def _prepare(self, tmp_path: Path) -> tuple[FakeIngestClient, Path, Path]:
        directory = tmp_path / "trip"
        directory.mkdir()
        (directory / "a.jpg").write_bytes(b"\xff\xd8\xff")
        return FakeIngestClient(), directory, tmp_path / "albums"

    def _ingestor(self, client: FakeIngestClient, albums_dir: Path) -> DirectoryIngestor:
        return DirectoryIngestor(
            client,
            albums_dir=albums_dir,
            poll_timeout_s=1.0,
            sleep=lambda _s: None,
        )

    def test_rerun_without_force_raises_before_uploading(self, tmp_path: Path) -> None:
        client, directory, albums_dir = self._prepare(tmp_path)
        self._ingestor(client, albums_dir).ingest(directory)  # first import
        client.uploaded.clear()

        with pytest.raises(IngestError, match="already exists"):
            self._ingestor(client, albums_dir).ingest(directory)  # second, no force
        assert client.uploaded == []  # guard fired before any upload

    def test_force_reruns_existing_without_reupload_or_new_album(self, tmp_path: Path) -> None:
        client, directory, albums_dir = self._prepare(tmp_path)
        self._ingestor(client, albums_dir).ingest(directory)  # first import
        created_before = len(client.created)
        client.uploaded.clear()

        slug = self._ingestor(client, albums_dir).ingest(directory, force=True)

        assert slug == "trip"
        # --force re-runs the existing album as is: no re-upload, no duplicate
        # Immich album (the pipeline is load-once, so it reuses the existing row).
        assert client.uploaded == []
        assert len(client.created) == created_before

    def test_force_preserves_hand_authored_album_yml(self, tmp_path: Path) -> None:
        client, directory, albums_dir = self._prepare(tmp_path)
        self._ingestor(client, albums_dir).ingest(directory)
        album_yml = albums_dir / "trip" / "album.yml"
        # The user hand-edits the generated album.yml to add segments/meta.
        album_yml.write_text(album_yml.read_text() + "meta:\n  note: hand-authored\n")

        self._ingestor(client, albums_dir).ingest(directory, force=True)

        # A forced re-run leaves the file untouched — it does not clobber edits.
        assert "hand-authored" in album_yml.read_text()


class TestLeadFacePatch:
    """`patch_lead_faces` and the ``--force --lead-face`` reuse path."""

    def _prepare(self, tmp_path: Path) -> tuple[FakeIngestClient, Path, Path]:
        directory = tmp_path / "trip"
        directory.mkdir()
        (directory / "a.jpg").write_bytes(b"\xff\xd8\xff")
        return FakeIngestClient(), directory, tmp_path / "albums"

    def _ingestor(self, client: FakeIngestClient, albums_dir: Path) -> DirectoryIngestor:
        return DirectoryIngestor(
            client,
            albums_dir=albums_dir,
            poll_timeout_s=1.0,
            sleep=lambda _s: None,
        )

    def test_force_with_lead_faces_patches_existing_album_yml(self, tmp_path: Path) -> None:
        client, directory, albums_dir = self._prepare(tmp_path)
        self._ingestor(client, albums_dir).ingest(directory)  # first import, no leads
        album_yml = albums_dir / "trip" / "album.yml"
        assert "lead_faces" not in yaml.safe_load(album_yml.read_text())
        client.uploaded.clear()  # isolate the forced re-run's uploads
        lead = tmp_path / "lead.jpg"
        lead.write_bytes(b"\xff\xd8\xff")

        slug = self._ingestor(client, albums_dir).ingest(
            directory,
            force=True,
            lead_face_images=[lead],
        )

        # --force + --lead-face patches leads onto the existing album without a
        # re-upload/new Immich album; other keys (slug/title) are preserved.
        assert slug == "trip"
        assert client.uploaded == []
        data = yaml.safe_load(album_yml.read_text())
        assert data["slug"] == "trip"
        assert data["lead_faces"] == {"reference_images": [str(lead.resolve())]}

    def test_patch_lead_faces_replaces_an_existing_block(self, tmp_path: Path) -> None:
        album_yml = tmp_path / "album.yml"
        album_yml.write_text(
            "slug: s\ntitle: T\nlead_faces:\n  person_ids: [p-old]\n",
        )
        new_lead = tmp_path / "new.jpg"
        new_lead.write_bytes(b"\xff\xd8\xff")

        patch_lead_faces(album_yml, [new_lead])

        data = yaml.safe_load(album_yml.read_text())
        # The old block is replaced wholesale, not merged.
        assert data["lead_faces"] == {"reference_images": [str(new_lead.resolve())]}
        assert (data["slug"], data["title"]) == ("s", "T")

    def test_patch_lead_faces_missing_album_yml_raises(self, tmp_path: Path) -> None:
        lead = tmp_path / "lead.jpg"
        lead.write_bytes(b"\xff\xd8\xff")

        with pytest.raises(IngestError, match="no album.yml"):
            patch_lead_faces(tmp_path / "nope" / "album.yml", [lead])

    def test_patch_lead_faces_empty_album_yml_raises(self, tmp_path: Path) -> None:
        # A present-but-empty file must not be silently turned into a leads-only
        # album.yml (which would strip slug/title and fail the loader cryptically).
        album_yml = tmp_path / "album.yml"
        album_yml.write_text("")
        lead = tmp_path / "lead.jpg"
        lead.write_bytes(b"\xff\xd8\xff")

        with pytest.raises(IngestError, match="empty or malformed"):
            patch_lead_faces(album_yml, [lead])


class TestReadinessPoll:
    """The Immich async-processing wait before the pipeline runs."""

    def _run(self, tmp_path: Path, client: FakeIngestClient, **kw: object) -> None:
        directory = tmp_path / "trip"
        directory.mkdir()
        (directory / "a.jpg").write_bytes(b"\xff\xd8\xff")
        DirectoryIngestor(
            client,
            albums_dir=tmp_path / "albums",
            poll_interval_s=2.0,
            poll_timeout_s=10.0,
            sleep=lambda _s: None,
            **kw,  # pyright: ignore[reportArgumentType]
        ).ingest(directory)

    def test_polls_through_busy_then_proceeds(self, tmp_path: Path) -> None:
        # EXIF still extracting for two polls, then idle.
        client = FakeIngestClient(jobs=[_busy(active=2, waiting=5), _busy(waiting=3), _busy()])

        self._run(tmp_path, client)

        # Polled until the queue reported idle (not resolving on the first busy call).
        assert client.jobs_calls == 3
        assert (tmp_path / "albums" / "trip" / "album.yml").exists()

    def test_timeout_warns_and_continues(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = FakeIngestClient(jobs=[_busy(active=1)])  # never goes idle

        with caplog.at_level(logging.WARNING):
            self._run(tmp_path, client)

        assert "still processing" in caplog.text
        # Degrades gracefully: the album.yml is still written so the pipeline runs.
        assert (tmp_path / "albums" / "trip" / "album.yml").exists()

    def test_unreported_queue_treated_as_idle(self, tmp_path: Path) -> None:
        client = FakeIngestClient(jobs=[{}])  # Immich reports no queues at all

        self._run(tmp_path, client)

        assert client.jobs_calls == 1  # idle on the first check, no waiting


class TestSourceDocument:
    """`--source-document`: the segments agent fills `album.yml`'s segments block."""

    def _canned_model(self) -> FunctionModel:
        """A model that returns one fixed segment + title, so the wiring (not the
        LLM) is what's under test — the agent itself is covered in test_segments."""
        canned = (
            '{"title": "Adriatic Days", "segments": [{"label": "Lisbon", "place": "Lisbon", '
            '"start_at": "2026-05-01T09:00:00+00:00", "end_at": "2026-05-01T18:00:00+00:00"}]}'
        )

        def handler(
            messages: list[ModelMessage],
            info: AgentInfo,
        ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
            return ModelResponse(parts=[TextPart(content=canned)])

        return FunctionModel(handler)

    def _photos_dir(self, tmp_path: Path) -> Path:
        directory = tmp_path / "raw-dump"
        directory.mkdir()
        (directory / "a.jpg").write_bytes(b"\xff\xd8\xff")
        return directory

    def _pdf(self, tmp_path: Path) -> Path:
        from weasyprint import HTML

        doc = tmp_path / "schedule.pdf"
        pdf = HTML(string="<html><body><p>Day 1 — Lisbon. Day 2 — Perast.</p></body></html>")
        doc.write_bytes(pdf.write_pdf() or b"")
        return doc

    def _ingestor(self, client: FakeIngestClient, albums_dir: Path) -> DirectoryIngestor:
        return DirectoryIngestor(
            client,
            albums_dir=albums_dir,
            poll_timeout_s=1.0,
            sleep=lambda _s: None,
            model=self._canned_model(),
        )

    def test_fills_segments_and_uses_doc_title(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path)
        albums_dir = tmp_path / "albums"

        self._ingestor(FakeIngestClient(), albums_dir).ingest(
            directory,
            source_document=self._pdf(tmp_path),
        )

        album_yml = albums_dir / "raw-dump" / "album.yml"
        written = yaml.safe_load(album_yml.read_text())
        # Doc title feeds precedence #2 (over the directory name).
        assert written["title"] == "Adriatic Days"
        assert written["segments"][0]["label"] == "Lisbon"
        # What was written is a valid album.yml — segments round-trip through the
        # loader, which normalizes/validates them (the source of truth).
        loaded = AlbumDefinition().load(album_yml)
        assert [s.label for s in loaded.segments] == ["Lisbon"]

    def test_explicit_title_beats_doc_title(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path)
        albums_dir = tmp_path / "albums"

        self._ingestor(FakeIngestClient(), albums_dir).ingest(
            directory,
            title="Our Own Title",
            source_document=self._pdf(tmp_path),
        )

        written = yaml.safe_load((albums_dir / "raw-dump" / "album.yml").read_text())
        assert written["title"] == "Our Own Title"
        assert written["segments"][0]["label"] == "Lisbon"  # segments still filled

    def test_no_source_document_writes_no_segments(self, tmp_path: Path) -> None:
        directory = self._photos_dir(tmp_path)
        albums_dir = tmp_path / "albums"

        self._ingestor(FakeIngestClient(), albums_dir).ingest(directory)

        written = yaml.safe_load((albums_dir / "raw-dump" / "album.yml").read_text())
        assert "segments" not in written

    def test_invalid_extracted_segments_fail_fast_with_guidance(self, tmp_path: Path) -> None:
        # Naive timestamps + no --timezone: the loader can't resolve them to UTC.
        # Ingest must surface a clear IngestError here, not after "success".
        naive = (
            '{"segments": [{"label": "X", '
            '"start_at": "2026-05-01T09:00:00", "end_at": "2026-05-01T18:00:00"}]}'
        )

        def handler(
            messages: list[ModelMessage],
            info: AgentInfo,
        ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
            return ModelResponse(parts=[TextPart(content=naive)])

        directory = self._photos_dir(tmp_path)
        albums_dir = tmp_path / "albums"
        ingestor = DirectoryIngestor(
            client=FakeIngestClient(),
            albums_dir=albums_dir,
            poll_timeout_s=1.0,
            sleep=lambda _s: None,
            model=FunctionModel(handler),
        )

        with pytest.raises(IngestError, match="invalid"):
            ingestor.ingest(directory, source_document=self._pdf(tmp_path))
        # The file is left on disk so the user can fix it and re-run with --force.
        assert (albums_dir / "raw-dump" / "album.yml").exists()

"""S124 — SnapshotFileService validation, uniform list, zip, events (fakes)."""
import io
import zipfile
from uuid import uuid4

import pytest
from vbwd.services.filesystem.memory import InMemoryFilesystemManager

from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.models.dataset_snapshot import DatasetSnapshot
from plugins.dataset.dataset.models.dataset_snapshot_file import (
    FILE_ROLE_CHART,
    FILE_ROLE_DATA,
    FILE_ROLE_DOCUMENT,
    FILE_ROLE_OTHER,
)
from plugins.dataset.dataset.services.dataset_service import (
    DatasetSnapshotNotFoundError,
    EVENT_DATASET_UPDATED,
)
from plugins.dataset.dataset.services.snapshot_file_service import (
    DisallowedFileExtensionError,
    FileTooLargeError,
    InvalidFileRoleError,
    PRIMARY_FILE_ID,
    SnapshotFileService,
)
from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend


class RecordingBus:
    def __init__(self):
        self.events = []

    def publish(self, event_name, data):
        self.events.append((event_name, data))


class FakeSnapshotRepository:
    def __init__(self, snapshots=()):
        self._by_id = {str(snap.id): snap for snap in snapshots}

    def find_by_id(self, snapshot_id):
        return self._by_id.get(str(snapshot_id))


class FakeFileRepository:
    def __init__(self):
        self._by_id = {}

    def add(self, snapshot_file):
        if snapshot_file.id is None:
            snapshot_file.id = uuid4()
        self._by_id[str(snapshot_file.id)] = snapshot_file
        return snapshot_file

    def delete(self, snapshot_file):
        self._by_id.pop(str(snapshot_file.id), None)

    def find_by_id(self, file_id):
        return self._by_id.get(str(file_id))

    def find_for_snapshot(self, snapshot_id):
        return [
            row
            for row in self._by_id.values()
            if str(row.snapshot_id) == str(snapshot_id)
        ]


def _dataset():
    dataset = Dataset()
    dataset.id = uuid4()
    dataset.slug = "air-quality"
    return dataset


def _snapshot(dataset):
    snapshot = DatasetSnapshot(
        dataset_id=dataset.id,
        taken_at="2026-07-01-09-30",
        storage_backend="local",
        location="datasets/environment/air-quality/2026-07-01-09-30.csv",
        ext="csv",
        size_bytes=8,
        checksum="primsum",
    )
    snapshot.id = uuid4()
    return snapshot


def _service(dataset, snapshot, *, bus=None, backend=None, **kwargs):
    return SnapshotFileService(
        snapshot_file_repository=FakeFileRepository(),
        snapshot_repository=FakeSnapshotRepository([snapshot]),
        storage_backend=backend or LocalArchiveBackend(InMemoryFilesystemManager()),
        event_bus=bus if bus is not None else RecordingBus(),
        **kwargs,
    )


def test_add_file_persists_row_and_emits_updated():
    dataset = _dataset()
    snapshot = _snapshot(dataset)
    bus = RecordingBus()
    service = _service(dataset, snapshot, bus=bus)

    saved = service.add_file(
        str(dataset.id), str(snapshot.id), "report.pdf", FILE_ROLE_DOCUMENT, b"%PDF\n"
    )

    assert saved.role == FILE_ROLE_DOCUMENT
    assert saved.filename == "report.pdf"
    assert saved.content_type == "application/pdf"
    assert saved.location.endswith("2026-07-01-09-30/report.pdf")
    assert bus.events[-1][0] == EVENT_DATASET_UPDATED
    assert bus.events[-1][1]["slug"] == "air-quality"


def test_add_file_rejects_unknown_role():
    dataset = _dataset()
    snapshot = _snapshot(dataset)
    service = _service(dataset, snapshot)
    with pytest.raises(InvalidFileRoleError):
        service.add_file(
            str(dataset.id), str(snapshot.id), "report.pdf", "banner", b"x"
        )


def test_add_file_rejects_oversize():
    dataset = _dataset()
    snapshot = _snapshot(dataset)
    service = _service(dataset, snapshot, max_file_size_bytes=4)
    with pytest.raises(FileTooLargeError):
        service.add_file(
            str(dataset.id), str(snapshot.id), "a.csv", FILE_ROLE_DATA, b"toolong"
        )


def test_add_file_rejects_disallowed_extension():
    dataset = _dataset()
    snapshot = _snapshot(dataset)
    service = _service(dataset, snapshot, allowed_file_extensions=("csv", "pdf"))
    with pytest.raises(DisallowedFileExtensionError):
        service.add_file(
            str(dataset.id), str(snapshot.id), "evil.exe", FILE_ROLE_OTHER, b"x"
        )


def test_add_file_rejects_snapshot_of_other_dataset():
    dataset = _dataset()
    snapshot = _snapshot(dataset)
    service = _service(dataset, snapshot)
    with pytest.raises(DatasetSnapshotNotFoundError):
        service.add_file(str(uuid4()), str(snapshot.id), "a.csv", FILE_ROLE_DATA, b"x")


def test_list_issue_files_is_primary_first_then_members():
    dataset = _dataset()
    snapshot = _snapshot(dataset)
    service = _service(dataset, snapshot)
    service.add_file(
        str(dataset.id), str(snapshot.id), "chart.png", FILE_ROLE_CHART, b"png"
    )

    listing = service.list_issue_files(dataset, snapshot)

    assert listing[0]["id"] == PRIMARY_FILE_ID
    assert listing[0]["role"] == FILE_ROLE_DATA
    assert listing[0]["filename"] == "air-quality-2026-07-01-09-30.csv"
    assert listing[1]["role"] == FILE_ROLE_CHART
    # The uniform list NEVER exposes the raw storage location.
    assert all("location" not in entry for entry in listing)


def test_build_issue_archive_contains_primary_and_members():
    dataset = _dataset()
    snapshot = _snapshot(dataset)
    backend = LocalArchiveBackend(InMemoryFilesystemManager())
    # Materialise the primary bytes so the archive can read them back.
    backend.put("environment", "air-quality", "2026-07-01-09-30", "csv", b"city,aqi\n")
    service = SnapshotFileService(
        snapshot_file_repository=FakeFileRepository(),
        snapshot_repository=FakeSnapshotRepository([snapshot]),
        storage_backend=backend,
        event_bus=RecordingBus(),
    )
    service.add_file(
        str(dataset.id), str(snapshot.id), "report.pdf", FILE_ROLE_DOCUMENT, b"%PDF\n"
    )

    payload, filename = service.build_issue_archive(dataset, snapshot)

    assert filename == "air-quality-2026-07-01-09-30.zip"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert "air-quality-2026-07-01-09-30.csv" in names
        assert "report.pdf" in names
        assert archive.read("report.pdf") == b"%PDF\n"


def test_delete_file_removes_bytes_and_row():
    dataset = _dataset()
    snapshot = _snapshot(dataset)
    backend = LocalArchiveBackend(InMemoryFilesystemManager())
    service = SnapshotFileService(
        snapshot_file_repository=FakeFileRepository(),
        snapshot_repository=FakeSnapshotRepository([snapshot]),
        storage_backend=backend,
        event_bus=RecordingBus(),
    )
    saved = service.add_file(
        str(dataset.id), str(snapshot.id), "report.pdf", FILE_ROLE_DOCUMENT, b"%PDF\n"
    )
    assert backend.exists(saved.location)

    service.delete_file(str(dataset.id), str(snapshot.id), str(saved.id))

    assert not backend.exists(saved.location)
    assert service.list_issue_files(dataset, snapshot) == [
        service.list_issue_files(dataset, snapshot)[0]
    ]

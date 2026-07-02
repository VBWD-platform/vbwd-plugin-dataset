"""T2 — DatasetService events + snapshot-advances-last (MagicMock repos)."""
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.services.filesystem.memory import InMemoryFilesystemManager

from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.services.dataset_service import (
    EVENT_DATASET_NEW,
    EVENT_DATASET_UPDATED,
    DatasetService,
)
from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend


class RecordingBus:
    """Minimal EventBus double recording ``(name, data)`` publishes."""

    def __init__(self):
        self.events = []

    def publish(self, event_name, data):
        self.events.append((event_name, data))


def _service(dataset_repo, snapshot_repo, bus):
    backend = LocalArchiveBackend(InMemoryFilesystemManager())
    return DatasetService(dataset_repo, snapshot_repo, backend, bus)


def test_create_dataset_emits_dataset_new():
    dataset_repo = MagicMock()
    dataset_repo.save.side_effect = lambda dataset: dataset
    bus = RecordingBus()

    service = _service(dataset_repo, MagicMock(), bus)
    created = service.create_dataset({"slug": "air-quality", "title": "Air Quality"})

    assert created.slug == "air-quality"
    assert bus.events[0][0] == EVENT_DATASET_NEW
    assert bus.events[0][1]["slug"] == "air-quality"


def test_add_snapshot_advances_last_and_emits_updated():
    dataset = Dataset()
    dataset.id = uuid4()
    dataset.slug = "air-quality"
    dataset.last_snapshot_id = None

    dataset_repo = MagicMock()
    dataset_repo.find_by_id.return_value = dataset
    dataset_repo.save.side_effect = lambda saved: saved

    snapshot_id = uuid4()

    def _save_snapshot(snapshot):
        snapshot.id = snapshot_id
        return snapshot

    snapshot_repo = MagicMock()
    snapshot_repo.save.side_effect = _save_snapshot

    bus = RecordingBus()
    service = _service(dataset_repo, snapshot_repo, bus)

    snapshot = service.add_snapshot(
        str(dataset.id),
        data=b"col-a\n1\n",
        ext="csv",
        taken_at="2026-07-01-09-30",
        category_slug="environment",
    )

    assert snapshot.id == snapshot_id
    assert dataset.last_snapshot_id == snapshot_id  # last advanced
    assert snapshot.storage_backend == "local"
    assert snapshot.size_bytes == len(b"col-a\n1\n")
    assert bus.events[-1][0] == EVENT_DATASET_UPDATED
    assert bus.events[-1][1]["last_snapshot_id"] == str(snapshot_id)


def test_update_dataset_emits_dataset_updated():
    dataset = Dataset()
    dataset.id = uuid4()
    dataset.slug = "air-quality"

    dataset_repo = MagicMock()
    dataset_repo.find_by_id.return_value = dataset
    dataset_repo.save.side_effect = lambda saved: saved
    bus = RecordingBus()

    service = _service(dataset_repo, MagicMock(), bus)
    service.update_dataset(str(dataset.id), {"title": "Renamed"})

    assert dataset.title == "Renamed"
    assert bus.events[-1][0] == EVENT_DATASET_UPDATED

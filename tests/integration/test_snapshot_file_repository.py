"""S124 — DatasetSnapshotFileRepository CRUD + FK CASCADE on snapshot delete.

Exercises the real Flask app + PostgreSQL (rolled back per test).
"""
from uuid import uuid4

import pytest

from vbwd.extensions import db as _db

from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.models.dataset_snapshot import DatasetSnapshot
from plugins.dataset.dataset.models.dataset_snapshot_file import (
    FILE_ROLE_DOCUMENT,
    DatasetSnapshotFile,
)
from plugins.dataset.dataset.repositories.dataset_snapshot_file_repository import (
    DatasetSnapshotFileRepository,
)


def _make_snapshot(db):
    dataset = Dataset()
    dataset.slug = f"air-quality-{uuid4().hex[:8]}"
    dataset.title = "Air Quality"
    db.session.add(dataset)
    db.session.flush()

    snapshot = DatasetSnapshot(
        dataset_id=dataset.id,
        taken_at="2026-07-01-09-30",
        storage_backend="local",
        location="datasets/env/air-quality/2026-07-01-09-30.csv",
        ext="csv",
        size_bytes=10,
    )
    db.session.add(snapshot)
    db.session.flush()
    return snapshot


def _make_file(snapshot, filename="report.pdf"):
    return DatasetSnapshotFile(
        snapshot_id=snapshot.id,
        role=FILE_ROLE_DOCUMENT,
        filename=filename,
        storage_backend="local",
        location=f"datasets/env/air-quality/2026-07-01-09-30/{filename}",
        ext="pdf",
        content_type="application/pdf",
        size_bytes=100,
        checksum="abc",
    )


def test_add_find_and_delete_round_trip(db):
    snapshot = _make_snapshot(db)
    repo = DatasetSnapshotFileRepository(db.session)

    saved = repo.add(_make_file(snapshot))
    assert repo.find_by_id(str(saved.id)) is not None
    assert [row.id for row in repo.find_for_snapshot(str(snapshot.id))] == [saved.id]

    repo.delete(saved)
    assert repo.find_by_id(str(saved.id)) is None


def test_find_for_snapshot_orders_by_created_at(db):
    snapshot = _make_snapshot(db)
    repo = DatasetSnapshotFileRepository(db.session)

    first = repo.add(_make_file(snapshot, filename="a.pdf"))
    second = repo.add(_make_file(snapshot, filename="b.pdf"))

    ordered = repo.find_for_snapshot(str(snapshot.id))
    assert [row.id for row in ordered] == [first.id, second.id]


def test_snapshot_delete_cascades_to_files(db):
    snapshot = _make_snapshot(db)
    repo = DatasetSnapshotFileRepository(db.session)
    saved = repo.add(_make_file(snapshot))

    _db.session.delete(snapshot)
    _db.session.flush()

    assert repo.find_by_id(str(saved.id)) is None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

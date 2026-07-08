"""S124 — DatasetSnapshotFile role vocabulary + a leak-free ``to_dict``."""
from uuid import uuid4

from plugins.dataset.dataset.models.dataset_snapshot_file import (
    ALLOWED_FILE_ROLES,
    FILE_ROLE_CHART,
    FILE_ROLE_DATA,
    FILE_ROLE_DOCUMENT,
    FILE_ROLE_OTHER,
    DatasetSnapshotFile,
)


def test_allowed_roles_are_the_fixed_closed_set():
    assert ALLOWED_FILE_ROLES == (
        FILE_ROLE_DATA,
        FILE_ROLE_DOCUMENT,
        FILE_ROLE_CHART,
        FILE_ROLE_OTHER,
    )


def test_to_dict_never_leaks_the_raw_location():
    snapshot_file = DatasetSnapshotFile(
        id=uuid4(),
        snapshot_id=uuid4(),
        role=FILE_ROLE_DOCUMENT,
        filename="report.pdf",
        storage_backend="local",
        location="datasets/env/air-quality/2026-07-01-09-30/report.pdf",
        ext="pdf",
        content_type="application/pdf",
        size_bytes=1234,
        checksum="abc",
    )
    projected = snapshot_file.to_dict()

    assert "location" not in projected
    assert projected["role"] == FILE_ROLE_DOCUMENT
    assert projected["filename"] == "report.pdf"
    assert projected["ext"] == "pdf"
    assert projected["content_type"] == "application/pdf"
    assert projected["size_bytes"] == 1234
    assert projected["checksum"] == "abc"

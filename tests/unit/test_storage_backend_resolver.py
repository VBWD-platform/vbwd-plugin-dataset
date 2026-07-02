"""T3 — per-snapshot backend resolution (local + AWS) with graceful degrade.

The resolver picks the backend that holds a given snapshot from its
``storage_backend`` key, so ``Dataset.last`` is served through whichever backend
stored it. A missing/invalid AWS secret degrades gracefully: the AWS backend is
unavailable (a clear ``DatasetStorageError``), but local snapshots still serve —
callers of the port never crash (Liskov).
"""
import pytest

from vbwd.services.filesystem.memory import InMemoryFilesystemManager

from plugins.dataset.dataset.models.dataset_snapshot import (
    DatasetSnapshot,
    STORAGE_BACKEND_AWS,
    STORAGE_BACKEND_LOCAL,
)
from plugins.dataset.dataset.services.storage.aws_backend import AwsS3Backend
from plugins.dataset.dataset.services.storage.backend import DatasetStorageError
from plugins.dataset.dataset.services.storage.resolver import (
    DatasetStorageBackendResolver,
)
from plugins.dataset.tests.unit.test_aws_s3_backend import FakeS3Client


def _snapshot(backend_key, location):
    snapshot = DatasetSnapshot()
    snapshot.storage_backend = backend_key
    snapshot.location = location
    return snapshot


def test_dataset_mixes_local_and_aws_snapshots_both_resolve():
    filesystem_manager = InMemoryFilesystemManager()
    aws_backend = AwsS3Backend(FakeS3Client(), bucket="b", prefix="datasets")
    resolver = DatasetStorageBackendResolver(
        filesystem_manager, config={}, aws_backend=aws_backend
    )

    local_backend = resolver.default_backend()
    local_stored = local_backend.put("env", "air", "2026-07-01-09-30", "csv", b"local")
    aws_stored = aws_backend.put("env", "air", "2026-07-01-10-00", "csv", b"aws")

    local_snapshot = _snapshot(STORAGE_BACKEND_LOCAL, local_stored.location)
    aws_snapshot = _snapshot(STORAGE_BACKEND_AWS, aws_stored.location)

    assert resolver.for_snapshot(local_snapshot).open(local_stored.location) == b"local"
    assert resolver.for_snapshot(aws_snapshot).open(aws_stored.location) == b"aws"


def test_missing_secret_disables_aws_but_local_still_serves():
    filesystem_manager = InMemoryFilesystemManager()
    # No pre-built AWS backend and no aws config → the factory yields None
    # (missing secret) rather than crashing.
    resolver = DatasetStorageBackendResolver(
        filesystem_manager, config={"aws": {"enabled": False}}
    )

    local_backend = resolver.default_backend()
    stored = local_backend.put("env", "air", "2026-07-01-09-30", "csv", b"still-here")
    local_snapshot = _snapshot(STORAGE_BACKEND_LOCAL, stored.location)

    # Local keeps working …
    assert resolver.for_snapshot(local_snapshot).open(stored.location) == b"still-here"

    # … while an AWS snapshot degrades to a clear, catchable error (no crash).
    aws_snapshot = _snapshot(STORAGE_BACKEND_AWS, "datasets/env/air/x.csv")
    with pytest.raises(DatasetStorageError):
        resolver.for_snapshot(aws_snapshot)


def test_build_aws_backend_returns_none_without_credentials():
    from plugins.dataset.dataset.services.storage.aws_backend import build_aws_backend

    # enabled but no bucket / secret → None (graceful), never an exception.
    assert build_aws_backend({"aws": {"enabled": True}}) is None
    assert build_aws_backend({"aws": {"enabled": False, "bucket": "b"}}) is None
    assert build_aws_backend({}) is None

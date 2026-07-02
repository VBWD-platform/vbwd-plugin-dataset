"""T2 — LocalArchiveBackend stores/reads via the core filesystem manager."""
import hashlib

from vbwd.services.filesystem.memory import InMemoryFilesystemManager

from plugins.dataset.dataset.services.storage.local_backend import (
    ARCHIVE_PREFIX,
    LocalArchiveBackend,
)


def _backend():
    return LocalArchiveBackend(InMemoryFilesystemManager())


def test_put_uses_the_archive_path_convention():
    backend = _backend()
    stored = backend.put(
        category_slug="environment",
        dataset_slug="air-quality",
        taken_at="2026-07-01-09-30",
        ext="csv",
        data=b"col-a,col-b\n1,2\n",
    )
    assert stored.location == (
        f"{ARCHIVE_PREFIX}/environment/air-quality/2026-07-01-09-30.csv"
    )
    assert stored.ext == "csv"
    assert stored.size_bytes == len(b"col-a,col-b\n1,2\n")


def test_put_then_open_round_trips_bytes_and_checksum():
    backend = _backend()
    payload = b"hello,world\n"
    stored = backend.put("cat", "slug", "2026-07-01-10-00", "csv", payload)

    assert backend.exists(stored.location)
    assert backend.open(stored.location) == payload
    assert stored.checksum == hashlib.sha256(payload).hexdigest()


def test_delete_removes_the_stored_bytes():
    backend = _backend()
    stored = backend.put("cat", "slug", "2026-07-01-10-00", "csv", b"x")
    backend.delete(stored.location)
    assert not backend.exists(stored.location)


def test_uncategorized_default_when_no_category():
    backend = _backend()
    stored = backend.put("", "slug", "2026-07-01-10-00", "csv", b"x")
    assert stored.location.startswith(f"{ARCHIVE_PREFIX}/uncategorized/slug/")

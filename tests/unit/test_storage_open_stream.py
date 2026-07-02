"""T8 — the storage port exposes a lazy byte-chunk stream for reads.

``open_stream`` lets the read API / preview consume a snapshot without loading
the whole file first. The in-memory double honours the same lazy-iterator
contract (Liskov) as the production disk backend.
"""
from vbwd.services.filesystem.memory import InMemoryFilesystemManager

from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend


def _backend():
    return LocalArchiveBackend(InMemoryFilesystemManager())


def test_open_stream_round_trips_the_stored_bytes():
    backend = _backend()
    payload = b"col-a,col-b\n1,2\n3,4\n"
    stored = backend.put("env", "air-quality", "2026-07-01-09-30", "csv", payload)

    streamed = b"".join(backend.open_stream(stored.location))
    assert streamed == payload


def test_open_stream_yields_chunks():
    backend = _backend()
    payload = b"x" * 50
    stored = backend.put("cat", "slug", "2026-07-01-10-00", "bin", payload)

    chunks = list(backend.open_stream(stored.location, chunk_size=8))
    assert len(chunks) > 1  # streamed, not one blob
    assert b"".join(chunks) == payload

"""Storage-backend port for dataset archives (S110 T2, DIP).

The plugin owns this port so the read API, dashboard and admin CRUD depend on
the abstraction, never on a concrete storage. ``LocalArchiveBackend`` (the MVP
default, over the core filesystem ``var`` namespace) implements it here; an
optional ``AwsS3Backend`` implements the same contract in S110 T3.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

# Default read chunk size for streaming a snapshot's bytes (64 KiB).
DEFAULT_STREAM_CHUNK_SIZE = 65536


@dataclass(frozen=True)
class StoredSnapshot:
    """The result of persisting one snapshot's bytes.

    Attributes:
        location: backend-relative path/key where the bytes live (never a
            public URL).
        size_bytes: number of bytes written.
        checksum: hex digest of the bytes (integrity + de-dup).
        ext: file extension (without the leading dot), echoed for convenience.
    """

    location: str
    size_bytes: int
    checksum: str
    ext: str


class DatasetStorageError(Exception):
    """Raised when a storage backend cannot complete an operation."""


class IDatasetStorageBackend(ABC):
    """The single, backend-agnostic way to store and read dataset archives."""

    #: Stable key persisted on ``DatasetSnapshot.storage_backend`` and used to
    #: resolve the backend for a read.
    backend_key: str = ""

    @abstractmethod
    def put(
        self,
        category_slug: str,
        dataset_slug: str,
        taken_at: str,
        ext: str,
        data: bytes,
    ) -> StoredSnapshot:
        """Persist ``data`` for one snapshot and return its stored metadata."""

    @abstractmethod
    def open(self, location: str) -> bytes:
        """Return the raw bytes stored at ``location``."""

    @abstractmethod
    def open_stream(
        self, location: str, chunk_size: int = DEFAULT_STREAM_CHUNK_SIZE
    ) -> Iterator[bytes]:
        """Yield the bytes at ``location`` lazily, chunk by chunk.

        Consumers (the read API, the capped preview) pull only as many chunks as
        they need, so a large snapshot is never fully materialised to serve or to
        slice its first rows.
        """

    @abstractmethod
    def exists(self, location: str) -> bool:
        """Return True if ``location`` holds stored bytes."""

    @abstractmethod
    def delete(self, location: str) -> None:
        """Remove the bytes at ``location``; a no-op if absent."""

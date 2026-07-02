"""Local archive backend — dataset files on the core filesystem (S110 T2).

The MVP default. All I/O routes through the core ``FilesystemManager`` (resolved
via ``container.filesystem_manager()``), never raw ``os``: the manager confines
every path within its namespace (defeating ``..`` / symlink escape) and applies
the namespace write policy.

The dataset plugin owns its filespace at ``var/dataset/…`` via the core
``FilesystemManager`` generic plugin-owned namespace (the plugin id IS the
namespace — core never enumerates plugin ids). On disk:
``<var_root>/dataset/datasets/<category-slug>/<dataset-slug>/<ts>.<ext>``.

Path convention (backend-relative ``location`` stored on the snapshot):
``datasets/<category-slug>/<dataset-slug>/<YYYY-MM-DD-HH-mm>.<ext>`` — relative
to the plugin filespace root, so a snapshot row resolves under ``var/dataset/``.
"""
import hashlib
import os
from typing import Iterator

from plugins.dataset.dataset.services.storage.backend import (
    DEFAULT_STREAM_CHUNK_SIZE,
    IDatasetStorageBackend,
    StoredSnapshot,
)
from plugins.dataset.dataset.models.dataset_snapshot import STORAGE_BACKEND_LOCAL

# The plugin's own var namespace (== plugin id) and the relative prefix the
# archive lives under. A plugin namespace is non-served, so files are never
# exposed as public URLs (they are served only through the entitlement-gated
# read API).
DEFAULT_NAMESPACE = "dataset"
ARCHIVE_PREFIX = "datasets"


class LocalArchiveBackend(IDatasetStorageBackend):
    """Stores dataset snapshots on the local filesystem via the manager."""

    backend_key = STORAGE_BACKEND_LOCAL

    def __init__(self, filesystem_manager, namespace: str = DEFAULT_NAMESPACE) -> None:
        """Wrap the core ``FilesystemManager``.

        Args:
            filesystem_manager: an ``IFilesystemManager`` (production) or the
                in-memory double (tests), resolved via
                ``container.filesystem_manager()``.
            namespace: the plugin's own var namespace (defaults to ``dataset``,
                so files land at ``var/dataset/…`` — the plugin's filespace).
        """
        self._filesystem_manager = filesystem_manager
        self._namespace = namespace

    def _build_location(
        self, category_slug: str, dataset_slug: str, taken_at: str, ext: str
    ) -> str:
        """Compose the backend-relative archive path (single home, DRY)."""
        safe_category = category_slug or "uncategorized"
        return f"{ARCHIVE_PREFIX}/{safe_category}/{dataset_slug}/{taken_at}.{ext}"

    def put(
        self,
        category_slug: str,
        dataset_slug: str,
        taken_at: str,
        ext: str,
        data: bytes,
    ) -> StoredSnapshot:
        location = self._build_location(category_slug, dataset_slug, taken_at, ext)
        self._filesystem_manager.write_bytes(self._namespace, location, data)
        return StoredSnapshot(
            location=location,
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            ext=ext,
        )

    def open(self, location: str) -> bytes:
        return self._filesystem_manager.read_bytes(self._namespace, location)

    def open_stream(
        self, location: str, chunk_size: int = DEFAULT_STREAM_CHUNK_SIZE
    ) -> Iterator[bytes]:
        """Yield the snapshot bytes lazily.

        On production disk (``LocalFilesystemManager``) the confined path resolves
        to a real file, so the bytes are read chunk by chunk straight off disk —
        a consumer that stops early (the capped preview) never reads past what it
        needs. When the manager is the in-memory test double the resolved path is
        not a real file, so we honour the same lazy-iterator contract by chunking
        the already-materialised bytes (tests use tiny files, so no memory cost).
        The plugin namespace is not encrypted at rest, so raw disk bytes equal
        the stored bytes.
        """
        resolved_path = self._resolve_real_file(location)
        if resolved_path is not None:
            with open(resolved_path, "rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            return

        data = self.open(location)
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]

    def _resolve_real_file(self, location: str):
        """Return the confined on-disk path for ``location`` when it is a real
        file, else ``None`` (e.g. the in-memory manager, or a missing file)."""
        try:
            candidate = self._filesystem_manager.resolve(self._namespace, location)
        except (ValueError, AttributeError):
            return None
        if candidate and os.path.isfile(candidate):
            return candidate
        return None

    def exists(self, location: str) -> bool:
        return self._filesystem_manager.exists(self._namespace, location)

    def delete(self, location: str) -> None:
        self._filesystem_manager.delete(self._namespace, location)

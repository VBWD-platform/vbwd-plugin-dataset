"""Per-snapshot storage-backend resolution (S110 T3).

A dataset's snapshots may live in different backends (local by default, some in
S3). This resolver maps a snapshot's ``storage_backend`` key to the concrete
backend that holds its bytes, so the read API / download / preview serve
``Dataset.last`` through whichever backend stored it — without the routes
knowing which one.

Graceful degradation (Liskov): local is always available; AWS is available only
when configured with a valid secret. Requesting an AWS snapshot while AWS is
unavailable raises a clear, catchable :class:`DatasetStorageError` (the read
route answers 503) rather than crashing — local snapshots keep serving.
"""
from typing import Any, Callable, Dict, Optional

from plugins.dataset.dataset.models.dataset_snapshot import STORAGE_BACKEND_AWS
from plugins.dataset.dataset.services.storage.aws_backend import build_aws_backend
from plugins.dataset.dataset.services.storage.backend import (
    DatasetStorageError,
    IDatasetStorageBackend,
)
from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend


class DatasetStorageBackendResolver:
    """Resolves the storage backend that holds a given snapshot."""

    def __init__(
        self,
        filesystem_manager: Any,
        config: Optional[Dict[str, Any]] = None,
        *,
        aws_backend: Optional[IDatasetStorageBackend] = None,
        aws_backend_factory: Callable[
            [Optional[Dict[str, Any]]], Optional[IDatasetStorageBackend]
        ] = build_aws_backend,
    ) -> None:
        """Wire the resolver.

        Args:
            filesystem_manager: the core ``FilesystemManager`` backing the local
                archive backend (the MVP default).
            config: the live plugin config (its ``aws`` block drives the AWS
                backend). ``None`` means AWS is off.
            aws_backend: a pre-built AWS backend (tests inject a fake-client one);
                when given it is used as-is instead of the factory.
            aws_backend_factory: builds the AWS backend from config, returning
                ``None`` when AWS is unavailable (missing secret / boto3).
        """
        self._local_backend = LocalArchiveBackend(filesystem_manager)
        self._config = config or {}
        self._aws_backend = aws_backend
        self._aws_backend_factory = aws_backend_factory

    def default_backend(self) -> IDatasetStorageBackend:
        """The MVP default backend for writes — local (the core filesystem)."""
        return self._local_backend

    def for_backend_key(self, backend_key: str) -> IDatasetStorageBackend:
        """Return the backend for a ``storage_backend`` key.

        Raises :class:`DatasetStorageError` when the requested backend is
        configured-but-unavailable (an AWS snapshot while AWS is off), so the
        caller degrades gracefully instead of crashing.
        """
        if backend_key == STORAGE_BACKEND_AWS:
            aws_backend = self._resolve_aws_backend()
            if aws_backend is None:
                raise DatasetStorageError(
                    "AWS storage backend is not available "
                    "(missing/invalid secret or boto3 absent)"
                )
            return aws_backend
        return self._local_backend

    def for_snapshot(self, snapshot: Any) -> IDatasetStorageBackend:
        """Return the backend that holds ``snapshot``'s bytes."""
        return self.for_backend_key(snapshot.storage_backend)

    def _resolve_aws_backend(self) -> Optional[IDatasetStorageBackend]:
        if self._aws_backend is not None:
            return self._aws_backend
        return self._aws_backend_factory(self._config)

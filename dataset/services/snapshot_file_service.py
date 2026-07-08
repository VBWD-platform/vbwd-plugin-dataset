"""SnapshotFileService — companion-file lifecycle for a dataset issue (S124).

An *issue* (a ``DatasetSnapshot``) may carry N companion files. This service owns
their lifecycle so ``DatasetService`` stays focused on the catalogue + primary
snapshot (SRP):

* :meth:`add_file` — validate (role / size / extension), persist the bytes under
  the issue's per-issue folder via the storage port, save the row, emit
  ``dataset.updated``.
* :meth:`list_issue_files` — the **uniform** projection the FE renders: a
  synthesized ``role=data`` entry for the primary (stable id ``"primary"``)
  followed by the member rows. Never the raw ``location``.
* :meth:`get_file` — resolve one member row that belongs to the issue.
* :meth:`delete_file` — delete the bytes (idempotent) then the row.
* :meth:`build_issue_archive` — the whole issue (primary + members) as a zip.

Validation limits (max size, allowed extensions) are ops-tunable config, injected
by the composition root — never hardcoded here.
"""
import io
import mimetypes
import os
import zipfile
from typing import Iterable, List, Optional, Tuple

from plugins.dataset.dataset.models.dataset_snapshot_file import (
    ALLOWED_FILE_ROLES,
    FILE_ROLE_DATA,
    DatasetSnapshotFile,
)
from plugins.dataset.dataset.services.dataset_service import (
    DatasetSnapshotNotFoundError,
    EVENT_DATASET_UPDATED,
)
from plugins.dataset.dataset.services.storage.backend import IDatasetStorageBackend

# Ops-tunable fallbacks (mirrors ``DEFAULT_GRACE_PERIOD_DAYS``): the config in
# ``__init__.DEFAULT_CONFIG`` is the source of truth; these are the safety net the
# factory falls back to when a key is absent.
DEFAULT_MAX_FILE_SIZE_BYTES = 52428800  # 50 MiB
DEFAULT_ALLOWED_FILE_EXTENSIONS: Tuple[str, ...] = (
    "csv",
    "tsv",
    "json",
    "xlsx",
    "parquet",
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "svg",
    "txt",
    "md",
    "zip",
)

# The stable synthetic id the primary data file is exposed under in the uniform
# issue-file list (single source of truth; the download route branches on it).
PRIMARY_FILE_ID = "primary"


class DatasetSnapshotFileError(Exception):
    """Base for a bad member-file request (mapped to 400 by the routes)."""


class InvalidFileRoleError(DatasetSnapshotFileError):
    """Raised when a role is not one of ``ALLOWED_FILE_ROLES``."""


class FileTooLargeError(DatasetSnapshotFileError):
    """Raised when the payload exceeds the configured max file size."""


class DisallowedFileExtensionError(DatasetSnapshotFileError):
    """Raised when the file extension is not in the configured allow-list."""


class DatasetSnapshotFileNotFoundError(Exception):
    """Raised when a member file id does not resolve for the issue."""


class SnapshotFileService:
    """Coordinates an issue's companion files, their storage, and events."""

    def __init__(
        self,
        snapshot_file_repository,
        snapshot_repository,
        storage_backend: IDatasetStorageBackend,
        event_bus,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
        allowed_file_extensions: Iterable[str] = DEFAULT_ALLOWED_FILE_EXTENSIONS,
    ) -> None:
        self._files = snapshot_file_repository
        self._snapshots = snapshot_repository
        self._storage = storage_backend
        self._event_bus = event_bus
        self._max_file_size_bytes = int(max_file_size_bytes)
        self._allowed_extensions = {
            str(ext).lstrip(".").lower() for ext in allowed_file_extensions
        }

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def add_file(
        self, dataset_id: str, snapshot_id: str, filename: str, role: str, data: bytes
    ) -> DatasetSnapshotFile:
        """Validate and attach one companion file to an issue.

        Raises :class:`DatasetSnapshotNotFoundError` when the snapshot is unknown
        or belongs to a different dataset, and a :class:`DatasetSnapshotFileError`
        subclass on a bad role / oversize / disallowed extension.
        """
        snapshot = self._require_owned_snapshot(dataset_id, snapshot_id)
        self._validate_role(role)
        self._validate_size(data)
        ext = self._extract_ext(filename)
        self._validate_ext(ext)

        category_slug, dataset_slug = self._locate(snapshot)
        content_type = mimetypes.guess_type(filename)[0]
        stored = self._storage.put_member(
            category_slug=category_slug,
            dataset_slug=dataset_slug,
            taken_at=snapshot.taken_at,
            filename=filename,
            data=data,
        )
        snapshot_file = DatasetSnapshotFile(
            snapshot_id=snapshot.id,
            role=role,
            filename=os.path.basename(stored.location),
            storage_backend=self._storage.backend_key,
            location=stored.location,
            ext=ext,
            content_type=content_type,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
        )
        saved = self._files.add(snapshot_file)
        self._publish_updated(dataset_id, dataset_slug)
        return saved

    def delete_file(self, dataset_id: str, snapshot_id: str, file_id: str) -> None:
        """Delete a member's bytes (idempotent) then its row."""
        snapshot = self._require_owned_snapshot(dataset_id, snapshot_id)
        snapshot_file = self._require_member(snapshot, file_id)
        dataset_slug = self._locate(snapshot)[1]

        self._storage.delete(snapshot_file.location)
        self._files.delete(snapshot_file)
        self._publish_updated(dataset_id, dataset_slug)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_issue_files(self, dataset, snapshot) -> List[dict]:
        """The uniform issue-file list: primary synth first, then members.

        Each entry is ``{id, role, filename, ext, content_type, size_bytes,
        checksum}`` — never the raw ``location``.
        """
        primary = self._primary_entry(dataset, snapshot)
        members = [
            {
                "id": str(member.id),
                "role": member.role,
                "filename": member.filename,
                "ext": member.ext,
                "content_type": member.content_type,
                "size_bytes": member.size_bytes,
                "checksum": member.checksum,
            }
            for member in self._files.find_for_snapshot(str(snapshot.id))
        ]
        return [primary] + members

    def get_file(
        self, dataset_id: str, snapshot_id: str, file_id: str
    ) -> DatasetSnapshotFile:
        """Resolve one member row that belongs to the issue (else raise)."""
        snapshot = self._require_owned_snapshot(dataset_id, snapshot_id)
        return self._require_member(snapshot, file_id)

    def build_issue_archive(self, dataset, snapshot) -> Tuple[bytes, str]:
        """Assemble the whole issue (primary + members) into an in-memory zip."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            primary_name = self._primary_filename(dataset, snapshot)
            archive.writestr(primary_name, self._storage.open(snapshot.location))
            for member in self._files.find_for_snapshot(str(snapshot.id)):
                archive.writestr(member.filename, self._storage.open(member.location))
        buffer.seek(0)
        filename = f"{dataset.slug}-{snapshot.taken_at}.zip"
        return buffer.getvalue(), filename

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _primary_entry(self, dataset, snapshot) -> dict:
        filename = self._primary_filename(dataset, snapshot)
        return {
            "id": PRIMARY_FILE_ID,
            "role": FILE_ROLE_DATA,
            "filename": filename,
            "ext": snapshot.ext,
            "content_type": mimetypes.guess_type(filename)[0],
            "size_bytes": snapshot.size_bytes,
            "checksum": snapshot.checksum,
        }

    @staticmethod
    def _primary_filename(dataset, snapshot) -> str:
        return f"{dataset.slug}-{snapshot.taken_at}.{snapshot.ext}"

    def _require_owned_snapshot(self, dataset_id: str, snapshot_id: str):
        snapshot = self._snapshots.find_by_id(snapshot_id)
        if snapshot is None or str(snapshot.dataset_id) != str(dataset_id):
            raise DatasetSnapshotNotFoundError(snapshot_id)
        return snapshot

    def _require_member(self, snapshot, file_id: str) -> DatasetSnapshotFile:
        member = self._files.find_by_id(file_id)
        if member is None or str(member.snapshot_id) != str(snapshot.id):
            raise DatasetSnapshotFileNotFoundError(file_id)
        return member

    @staticmethod
    def _locate(snapshot) -> Tuple[str, str]:
        """Derive ``(category_slug, dataset_slug)`` from the primary location.

        The primary lives at ``<prefix>/<category>/<dataset>/<taken_at>.<ext>``;
        the member folder is a sibling ``.../<taken_at>/``. Using the tail
        segments keeps this robust to a multi-segment storage prefix.
        """
        segments = (snapshot.location or "").strip("/").split("/")
        if len(segments) < 3:
            return "uncategorized", (segments[-2] if len(segments) >= 2 else "dataset")
        return segments[-3], segments[-2]

    def _validate_role(self, role: str) -> None:
        if role not in ALLOWED_FILE_ROLES:
            raise InvalidFileRoleError(role)

    def _validate_size(self, data: bytes) -> None:
        if len(data) > self._max_file_size_bytes:
            raise FileTooLargeError(len(data))

    @staticmethod
    def _extract_ext(filename: str) -> str:
        return os.path.splitext(filename or "")[1].lstrip(".").lower()

    def _validate_ext(self, ext: str) -> None:
        if ext not in self._allowed_extensions:
            raise DisallowedFileExtensionError(ext)

    def _publish_updated(self, dataset_id: str, dataset_slug: Optional[str]) -> None:
        """Broadcast a catalogue fact (no personal data) on the EventBus."""
        if self._event_bus is None:
            return
        self._event_bus.publish(
            EVENT_DATASET_UPDATED,
            {"dataset_id": str(dataset_id), "slug": dataset_slug},
        )

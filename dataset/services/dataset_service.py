"""DatasetService — catalogue CRUD, snapshot archive, and domain events (T2).

Business logic sits here, above the repositories and the storage backend
(depended on through their abstractions). It is the single home for the two
rules the sprint calls out:

* ``dataset.new`` is emitted when a dataset is created; ``dataset.updated`` when
  it changes OR when a new snapshot advances the ``last`` pointer.
* uploading a snapshot persists the bytes through the storage backend, records a
  ``DatasetSnapshot``, and advances ``Dataset.last_snapshot_id``.

Events broadcast catalogue facts only (no personal data), safe for the core
EventBus / outbound-webhook relay (GDPR).
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.models.dataset_plan import DatasetPlan
from plugins.dataset.dataset.models.dataset_snapshot import (
    DatasetSnapshot,
    INGESTED_VIA_UPLOAD,
)
from plugins.dataset.dataset.repositories.dataset_repository import (
    DEFAULT_SORT_COLUMN,
    DEFAULT_SORT_DIRECTION,
)
from plugins.dataset.dataset.services.storage.backend import IDatasetStorageBackend

EVENT_DATASET_NEW = "dataset.new"
EVENT_DATASET_UPDATED = "dataset.updated"

DEFAULT_CATEGORY_SLUG = "uncategorized"
TAKEN_AT_FORMAT = "%Y-%m-%d-%H-%M"

# Fields a caller may set on create/update. A closed list so an arbitrary body
# key can never reach the model.
WRITABLE_FIELDS = (
    "slug",
    "title",
    "description",
    "source_attribution",
    "price",
    "price_display_mode",
    "is_active",
)


class DatasetNotFoundError(Exception):
    """Raised when a dataset id/slug does not resolve."""


class DatasetSnapshotNotFoundError(Exception):
    """Raised when a snapshot id does not resolve (or belongs elsewhere)."""


def format_taken_at(moment: Optional[datetime] = None) -> str:
    """Return the ``YYYY-MM-DD-HH-mm`` token for ``moment`` (now if omitted)."""
    stamp = moment or datetime.now(timezone.utc)
    return stamp.strftime(TAKEN_AT_FORMAT)


class DatasetService:
    """Coordinates datasets, their snapshot archive, and domain events."""

    def __init__(
        self,
        dataset_repository,
        snapshot_repository,
        storage_backend: IDatasetStorageBackend,
        event_bus,
        dataset_plan_repository=None,
    ) -> None:
        self._datasets = dataset_repository
        self._snapshots = snapshot_repository
        self._storage = storage_backend
        self._event_bus = event_bus
        self._dataset_plans = dataset_plan_repository

    # ------------------------------------------------------------------
    # Catalogue CRUD
    # ------------------------------------------------------------------

    def create_dataset(self, data: Dict[str, Any]) -> Dataset:
        """Create a dataset and emit ``dataset.new``."""
        dataset = Dataset()
        self._apply_writable_fields(dataset, data)
        saved = self._datasets.save(dataset)
        self._publish(EVENT_DATASET_NEW, saved)
        return saved

    def update_dataset(self, dataset_id: str, data: Dict[str, Any]) -> Dataset:
        """Update a dataset and emit ``dataset.updated``."""
        dataset = self._require_dataset(dataset_id)
        self._apply_writable_fields(dataset, data)
        saved = self._datasets.save(dataset)
        self._publish(EVENT_DATASET_UPDATED, saved)
        return saved

    def delete_dataset(self, dataset_id: str) -> None:
        dataset = self._require_dataset(dataset_id)
        self._datasets.delete(dataset)

    def list_datasets(
        self,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = DEFAULT_SORT_COLUMN,
        sort_dir: str = DEFAULT_SORT_DIRECTION,
        search: Optional[str] = None,
        dataset_ids: Optional[List[str]] = None,
        active_only: bool = False,
    ) -> Dict[str, Any]:
        return self._datasets.find_all(
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_dir=sort_dir,
            search=search,
            dataset_ids=dataset_ids,
            active_only=active_only,
        )

    def get_dataset(self, dataset_id: str) -> Dataset:
        return self._require_dataset(dataset_id)

    def get_dataset_by_slug(self, slug: str) -> Dataset:
        """Resolve a dataset by its public slug (raises if unknown)."""
        dataset = self._datasets.find_by_slug(slug)
        if dataset is None:
            raise DatasetNotFoundError(slug)
        return dataset

    def resolve_snapshot(self, dataset: Dataset, taken_at: Optional[str] = None):
        """Return the snapshot to serve: the pinned ``taken_at`` or ``last``.

        Returns ``None`` when the dataset has no matching/last snapshot so the
        caller can answer 404 rather than expose an empty stream.
        """
        if taken_at:
            for snapshot in self._snapshots.find_for_dataset(str(dataset.id)):
                if snapshot.taken_at == taken_at:
                    return snapshot
            return None
        if not dataset.last_snapshot_id:
            return None
        return self._snapshots.find_by_id(str(dataset.last_snapshot_id))

    def slug_exists(self, slug: str) -> bool:
        """True if a dataset already uses ``slug`` (create-time uniqueness)."""
        return self._datasets.find_by_slug(slug) is not None

    def set_tariff_plan_link(self, dataset_id, tariff_plan_id) -> None:
        """Upsert the dataset's grant-plan link (MVP: a single plan per dataset).

        ``tariff_plan_id`` ``None``/empty clears the link; otherwise the dataset
        ends with exactly one ``DatasetPlan`` row pointing at that plan. Requires
        the plan repository to be wired (the admin composition root). Idempotent.
        """
        if self._dataset_plans is None:
            raise ValueError(
                "DatasetService.set_tariff_plan_link needs a dataset_plan_repository"
            )
        existing = self._dataset_plans.find_by_dataset_id(dataset_id)
        target = str(tariff_plan_id) if tariff_plan_id else None
        already_linked = any(str(link.tariff_plan_id) == target for link in existing)
        for link in existing:
            if target is None or str(link.tariff_plan_id) != target:
                self._dataset_plans.delete(link)
        if target and not already_linked:
            self._dataset_plans.save(
                DatasetPlan(dataset_id=dataset_id, tariff_plan_id=target)
            )

    # ------------------------------------------------------------------
    # Snapshot archive
    # ------------------------------------------------------------------

    def list_snapshots(self, dataset_id: str) -> List[DatasetSnapshot]:
        self._require_dataset(dataset_id)
        return self._snapshots.find_for_dataset(dataset_id)

    def get_snapshot(self, dataset_id: str, snapshot_id: str) -> DatasetSnapshot:
        """Resolve one snapshot that belongs to ``dataset_id``.

        Raises ``DatasetNotFoundError`` when the dataset is unknown and
        ``DatasetSnapshotNotFoundError`` when the snapshot is unknown or belongs
        to a different dataset (so the admin download route answers 404 rather
        than serving another dataset's bytes).
        """
        dataset = self._require_dataset(dataset_id)
        snapshot = self._snapshots.find_by_id(snapshot_id)
        if snapshot is None or str(snapshot.dataset_id) != str(dataset.id):
            raise DatasetSnapshotNotFoundError(snapshot_id)
        return snapshot

    def add_snapshot(
        self,
        dataset_id: str,
        data: bytes,
        ext: str,
        taken_at: Optional[str] = None,
        category_slug: str = DEFAULT_CATEGORY_SLUG,
        ingested_via: str = INGESTED_VIA_UPLOAD,
    ) -> DatasetSnapshot:
        """Persist bytes as a new snapshot, advance ``last``, emit updated.

        The stored ``location`` is backend-relative and never a public URL.
        """
        dataset = self._require_dataset(dataset_id)
        resolved_taken_at = taken_at or format_taken_at()

        stored = self._storage.put(
            category_slug=category_slug,
            dataset_slug=dataset.slug,
            taken_at=resolved_taken_at,
            ext=ext,
            data=data,
        )
        snapshot = DatasetSnapshot(
            dataset_id=dataset.id,
            taken_at=resolved_taken_at,
            storage_backend=self._storage.backend_key,
            location=stored.location,
            ext=stored.ext,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
            ingested_via=ingested_via,
        )
        saved_snapshot = self._snapshots.save(snapshot)

        dataset.last_snapshot_id = saved_snapshot.id
        self._datasets.save(dataset)
        self._publish(EVENT_DATASET_UPDATED, dataset)
        return saved_snapshot

    def set_last_snapshot(self, dataset_id: str, snapshot_id: str) -> Dataset:
        """Point ``last`` at an existing snapshot and emit ``dataset.updated``."""
        dataset = self._require_dataset(dataset_id)
        snapshot = self._snapshots.find_by_id(snapshot_id)
        if snapshot is None or str(snapshot.dataset_id) != str(dataset.id):
            raise DatasetSnapshotNotFoundError(snapshot_id)
        dataset.last_snapshot_id = snapshot.id
        saved = self._datasets.save(dataset)
        self._publish(EVENT_DATASET_UPDATED, saved)
        return saved

    def delete_snapshot(self, dataset_id: str, snapshot_id: str) -> None:
        """Delete a snapshot's row and bytes; clear ``last`` if it pointed here."""
        dataset = self._require_dataset(dataset_id)
        snapshot = self._snapshots.find_by_id(snapshot_id)
        if snapshot is None or str(snapshot.dataset_id) != str(dataset.id):
            raise DatasetSnapshotNotFoundError(snapshot_id)

        self._storage.delete(snapshot.location)
        self._snapshots.delete(snapshot)

        if dataset.last_snapshot_id and str(dataset.last_snapshot_id) == str(
            snapshot_id
        ):
            dataset.last_snapshot_id = None
            self._datasets.save(dataset)
            self._publish(EVENT_DATASET_UPDATED, dataset)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_writable_fields(self, dataset: Dataset, data: Dict[str, Any]) -> None:
        for field in WRITABLE_FIELDS:
            if field in data:
                setattr(dataset, field, data[field])

    def _require_dataset(self, dataset_id: str) -> Dataset:
        dataset = self._datasets.find_by_id(dataset_id)
        if dataset is None:
            raise DatasetNotFoundError(dataset_id)
        return dataset

    def _publish(self, event_name: str, dataset: Dataset) -> None:
        """Broadcast a catalogue fact (no personal data) on the EventBus."""
        if self._event_bus is None:
            return
        self._event_bus.publish(
            event_name,
            {
                "dataset_id": str(dataset.id),
                "slug": dataset.slug,
                "last_snapshot_id": (
                    str(dataset.last_snapshot_id) if dataset.last_snapshot_id else None
                ),
            },
        )

"""Dataset entity exchanger for the S46 data-exchange seam (S110 T10).

Datasets ride the SHARED data-exchange facilities (not a bespoke exporter):
``DatasetExchanger`` extends the generic :class:`BaseModelExchanger`, so it uses
the standard envelope ``{"vbwd_export":"dataset","version":1,"dataset":[rows]}``,
the registry, the routes and the CLI — core never names the dataset domain.

Two relationships travel with each dataset:

* **category by slug** — a dataset's ``dataset_category`` terms are exported as a
  ``category_slugs`` list via ``fk_natural_key_map`` (export-only, per the S46
  lesson) and resolved slug→id on import by this subclass's ``_import_row`` (an
  unknown slug is reported as a row error, never a crash — Liskov). Mirrors the
  subscription ``_CategoryExchanger`` / shop product-category precedent.
* **snapshot refs** — the dataset's snapshot metadata rows are carried nested
  under ``snapshots`` and the ``last`` pointer as the portable
  ``last_snapshot_taken_at`` (the local UUID never travels). On import the refs
  are recreated and ``last`` is restored through
  :meth:`DatasetService.set_last_snapshot` — the single home for the last/event
  logic (DRY; not duplicated here).

Engineering requirements (binding, restated): TDD-first; DevOps-first (cold
local + CI via the shared ``db`` fixture, no raw SQL); SOLID (one exchanger,
narrow ports); DI (session + repos injected); DRY (reuse ``BaseModelExchanger``
+ ``DatasetService`` for ``last``); Liskov (unknown slug → error row, never a
crash); clean code; no overengineering. No ghrm import. Quality guard:
``bin/pre-commit-check.sh --plugin dataset --full``.
"""
from typing import Any, List, Optional

from vbwd.services.data_exchange.base_model_exchanger import BaseModelExchanger
from vbwd.services.data_exchange.port import (
    CLUSTER_SALES,
    EntityExchanger,
    ImportResult,
)
from vbwd.services.data_exchange.registry import data_exchange_registry

from plugins.dataset import DATASET_CATEGORY_TERM_TYPE
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.models.dataset_snapshot import (
    DatasetSnapshot,
    INGESTED_VIA_SYNC,
    STORAGE_BACKEND_LOCAL,
)

# Existing dataset permissions (single source — declared in DatasetPlugin).
PERMISSION_VIEW = "dataset.view"
PERMISSION_MANAGE = "dataset.manage"

# The nested key that carries the category FK by slug (export via
# ``fk_natural_key_map``; resolved slug→id on import here).
CATEGORY_SLUGS_FIELD = "category_slugs"

# The nested keys that carry the snapshot archive + the ``last`` pointer.
SNAPSHOTS_FIELD = "snapshots"
LAST_SNAPSHOT_TAKEN_AT_FIELD = "last_snapshot_taken_at"

# Portable snapshot columns (the local UUID PK + ``dataset_id`` FK are rebound on
# import, so they never travel).
SNAPSHOT_PORTABLE_FIELDS = (
    "taken_at",
    "storage_backend",
    "location",
    "ext",
    "size_bytes",
    "checksum",
    "ingested_via",
)


class _SessionModelRepository:
    """Narrow model repo satisfying the ``BaseModelExchanger`` contract.

    Mirrors core's / CMS's / subscription's adapter: ``DatasetRepository`` exposes
    a paginated ``find_all`` and ``find_by_slug`` rather than the four flat
    methods the base exchanger needs, so this provides exactly those (ISP)
    without touching the existing repo.
    """

    def __init__(self, session: Any, model_class: type, natural_key: str) -> None:
        self._session = session
        self._model_class = model_class
        self._natural_key = natural_key

    def find_all(self) -> List[Any]:
        return self._session.query(self._model_class).all()

    def find_by_natural_key(self, value: Any) -> Optional[Any]:
        column = getattr(self._model_class, self._natural_key)
        return self._session.query(self._model_class).filter(column == value).first()

    def add(self, instance: Any) -> None:
        self._session.add(instance)

    def delete_all(self) -> None:
        self._session.query(self._model_class).delete()


class DatasetExchanger(BaseModelExchanger):
    """``dataset`` exchanger carrying its category (by slug) + snapshot refs."""

    def __init__(
        self,
        *,
        session: Any,
        dataset_repository: Any,
        snapshot_repository: Any,
        dataset_term_repository: Any,
        term_repository: Any,
        set_last_snapshot: Any,
    ) -> None:
        super().__init__(
            entity_key="dataset",
            label="Datasets",
            cluster=CLUSTER_SALES,
            natural_key="slug",
            model_class=Dataset,
            repository=dataset_repository,
            session=session,
            public_fields=[
                "slug",
                "title",
                "description",
                "source_attribution",
                "price",
                "price_display_mode",
                "is_active",
            ],
            fk_natural_key_map={CATEGORY_SLUGS_FIELD: self._export_category_slugs},
            supported_formats=frozenset({"json"}),
        )
        self._snapshots = snapshot_repository
        self._junction = dataset_term_repository
        self._terms = term_repository
        self._set_last_snapshot = set_last_snapshot

    # ── permissions (reuse the dataset RBAC, no parallel perm family) ─────

    @property
    def export_permission(self) -> str:
        return PERMISSION_VIEW

    @property
    def import_permission(self) -> str:
        return PERMISSION_MANAGE

    # ── export ───────────────────────────────────────────────────────────

    def _export_category_slugs(self, dataset: Any) -> List[str]:
        """Resolve a dataset's ``dataset_category`` term slugs (export-only FK)."""
        slugs: List[str] = []
        for term_id in self._junction.find_term_ids_by_dataset(str(dataset.id)):
            term = self._terms.find_by_id(term_id)
            if term is not None and term.slug:
                slugs.append(term.slug)
        return slugs

    def _serialise_row(self, row: Any, *, include_pii: bool) -> dict:
        serialised = super()._serialise_row(row, include_pii=include_pii)
        snapshots = self._snapshots.find_for_dataset(str(row.id))
        serialised[SNAPSHOTS_FIELD] = [
            self._serialise_snapshot(snapshot) for snapshot in snapshots
        ]
        serialised[LAST_SNAPSHOT_TAKEN_AT_FIELD] = self._resolve_last_taken_at(
            row, snapshots
        )
        return serialised

    def _serialise_snapshot(self, snapshot: Any) -> dict:
        return {
            field_name: getattr(snapshot, field_name)
            for field_name in SNAPSHOT_PORTABLE_FIELDS
        }

    def _resolve_last_taken_at(
        self, dataset: Any, snapshots: List[Any]
    ) -> Optional[str]:
        if not dataset.last_snapshot_id:
            return None
        for snapshot in snapshots:
            if str(snapshot.id) == str(dataset.last_snapshot_id):
                return snapshot.taken_at
        return None

    # ── import (resolves category slug→id, recreates snapshot refs) ───────

    def _import_row(
        self, row: dict, index: int, result: ImportResult, *, dry_run: bool
    ) -> None:
        category_slugs = row.pop(CATEGORY_SLUGS_FIELD, None) or []
        snapshots = row.pop(SNAPSHOTS_FIELD, None) or []
        last_taken_at = row.pop(LAST_SNAPSHOT_TAKEN_AT_FIELD, None)

        super()._import_row(row, index, result, dry_run=dry_run)
        if dry_run:
            return

        dataset = self._repository.find_by_natural_key(row.get(self.natural_key))
        if dataset is None:
            return
        self._apply_categories(dataset, category_slugs, index, result)
        self._apply_snapshots(dataset, snapshots, last_taken_at)

    def _apply_categories(
        self,
        dataset: Any,
        category_slugs: List[str],
        index: int,
        result: ImportResult,
    ) -> None:
        """Re-link category terms by slug (skip-with-error on an unknown slug)."""
        for slug in category_slugs:
            term = self._terms.find_by_type_and_slug(DATASET_CATEGORY_TERM_TYPE, slug)
            if term is None:
                result.errors.append(
                    {
                        "row": index,
                        "reason": f"unknown dataset_category slug '{slug}'",
                    }
                )
                continue
            if not self._junction.exists(str(dataset.id), str(term.id)):
                self._junction.add(str(dataset.id), str(term.id))

    def _apply_snapshots(
        self,
        dataset: Any,
        snapshots: List[dict],
        last_taken_at: Optional[str],
    ) -> None:
        """Recreate snapshot refs (idempotent by ``taken_at``) + restore ``last``.

        ``last`` is set through :meth:`DatasetService.set_last_snapshot` so the
        last-advance + ``dataset.updated`` event logic has one home (DRY).
        """
        existing = {
            snapshot.taken_at: snapshot
            for snapshot in self._snapshots.find_for_dataset(str(dataset.id))
        }
        last_snapshot = None
        for snapshot_ref in snapshots:
            taken_at = snapshot_ref.get("taken_at")
            if not taken_at:
                continue
            snapshot = existing.get(taken_at)
            if snapshot is None:
                snapshot = self._snapshots.save(
                    self._build_snapshot(dataset, taken_at, snapshot_ref)
                )
                existing[taken_at] = snapshot
            if taken_at == last_taken_at:
                last_snapshot = snapshot
        if last_snapshot is not None:
            self._set_last_snapshot(str(dataset.id), str(last_snapshot.id))

    def _build_snapshot(
        self, dataset: Any, taken_at: str, snapshot_ref: dict
    ) -> DatasetSnapshot:
        return DatasetSnapshot(
            dataset_id=dataset.id,
            taken_at=taken_at,
            storage_backend=snapshot_ref.get("storage_backend", STORAGE_BACKEND_LOCAL),
            location=snapshot_ref.get("location", ""),
            ext=snapshot_ref.get("ext", "csv"),
            size_bytes=snapshot_ref.get("size_bytes", 0),
            checksum=snapshot_ref.get("checksum"),
            ingested_via=snapshot_ref.get("ingested_via", INGESTED_VIA_SYNC),
        )


def _build_last_snapshot_setter(session: Any) -> Any:
    """Return a ``set_last(dataset_id, snapshot_id)`` bound to ``DatasetService``.

    Delegates to the single home for the ``last`` pointer + ``dataset.updated``
    event (DRY). Built lazily so the storage backend / EventBus are resolved from
    the app container at import time (matching the routes' composition root).
    """

    def _set_last(dataset_id: str, snapshot_id: str) -> None:
        from flask import current_app

        from vbwd.events.bus import event_bus

        from plugins.dataset.dataset.repositories.dataset_repository import (
            DatasetRepository,
        )
        from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
            DatasetSnapshotRepository,
        )
        from plugins.dataset.dataset.services.dataset_service import DatasetService
        from plugins.dataset.dataset.services.storage.local_backend import (
            LocalArchiveBackend,
        )

        storage_backend = LocalArchiveBackend(
            current_app.container.filesystem_manager()
        )
        service = DatasetService(
            dataset_repository=DatasetRepository(session),
            snapshot_repository=DatasetSnapshotRepository(session),
            storage_backend=storage_backend,
            event_bus=event_bus,
        )
        service.set_last_snapshot(dataset_id, snapshot_id)

    return _set_last


def build_dataset_exchangers(session: Any) -> List[EntityExchanger]:
    """Construct the dataset exchangers bound to ``session``."""
    from plugins.cms.src.repositories.term_repository import TermRepository

    from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
        DatasetSnapshotRepository,
    )
    from plugins.dataset.dataset.repositories.dataset_term_repository import (
        DatasetTermRepository,
    )

    return [
        DatasetExchanger(
            session=session,
            dataset_repository=_SessionModelRepository(session, Dataset, "slug"),
            snapshot_repository=DatasetSnapshotRepository(session),
            dataset_term_repository=DatasetTermRepository(session),
            term_repository=TermRepository(session),
            set_last_snapshot=_build_last_snapshot_setter(session),
        ),
    ]


def register_dataset_exchangers(session: Any) -> None:
    """Register the dataset exchangers into the registry (idempotent).

    Called from ``DatasetPlugin.on_enable``. Re-registering replaces by key, so a
    repeat enable (per-test app) is clear-safe.
    """
    for exchanger in build_dataset_exchangers(session):
        data_exchange_registry.register(exchanger)

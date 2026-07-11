"""S128 — dataset ↔ cms entity-page wiring (integration).

Exercises the real Flask app + PostgreSQL (rolled back per test) against the
cms entity-page service resolved through the DI container:

  * deleting a dataset cleans up its attached entity page (the ``delete_hook``
    calls ``delete_for_owner("dataset", id)`` so the page + link are removed);
  * the public dataset detail endpoint surfaces the attached page's SEO under a
    ``page_seo`` key when a published page exists, and omits it otherwise.
"""
from uuid import uuid4

import pytest
from flask import current_app

from vbwd.events.bus import event_bus
from vbwd.extensions import db as _db

from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.repositories.dataset_repository import DatasetRepository
from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from plugins.dataset.dataset.services.dataset_service import DatasetService
from plugins.dataset.dataset.services.entity_page_bridge import (
    delete_dataset_entity_page,
)
from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend


@pytest.fixture
def client(app):
    return app.test_client()


def _catalogue_service() -> DatasetService:
    backend = LocalArchiveBackend(current_app.container.filesystem_manager())
    return DatasetService(
        dataset_repository=DatasetRepository(_db.session),
        snapshot_repository=DatasetSnapshotRepository(_db.session),
        storage_backend=backend,
        event_bus=event_bus,
    )


def _make_dataset(db):
    dataset = Dataset()
    dataset.slug = f"air-quality-{uuid4().hex[:8]}"
    dataset.title = "Air Quality"
    dataset.description = "Daily air-quality index by city"
    dataset.source_attribution = "Public data pipeline"
    dataset.price = 100.0
    dataset.is_active = True
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _entity_page_service():
    return current_app.container.cms_entity_page_service()


def _create_entity_page(dataset, *, meta_title):
    _entity_page_service().save(
        "dataset",
        str(dataset.id),
        "main",
        {
            "content_html": "<p>About this dataset</p>",
            "seo": {"meta_title": meta_title},
        },
    )
    _db.session.commit()


# ── delete cleanup ───────────────────────────────────────────────────────


def test_deleting_a_dataset_removes_its_entity_page(db):
    dataset = _make_dataset(db)
    _create_entity_page(dataset, meta_title="Air Quality dataset")
    # The page exists (published) before delete.
    assert _entity_page_service().public_view("dataset", str(dataset.id)) is not None

    _catalogue_service().delete_dataset(str(dataset.id))
    delete_dataset_entity_page(dataset.id)
    db.session.commit()

    # The attached page + link are gone.
    assert _entity_page_service().public_view("dataset", str(dataset.id)) is None


# ── public SEO passthrough ───────────────────────────────────────────────


def test_public_detail_includes_page_seo_when_a_page_exists(db, client):
    dataset = _make_dataset(db)
    _create_entity_page(dataset, meta_title="Air Quality dataset")

    response = client.get(f"/api/v1/dataset/{dataset.slug}")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert "page_seo" in payload
    assert payload["page_seo"]["meta_title"] == "Air Quality dataset"


def test_public_detail_omits_page_seo_when_no_page(db, client):
    dataset = _make_dataset(db)

    response = client.get(f"/api/v1/dataset/{dataset.slug}")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    # No attached page → the key is omitted (never a null smear).
    assert "page_seo" not in payload


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

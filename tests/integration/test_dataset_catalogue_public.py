"""Public catalogue reconciliation — the three fe-user-consumed read endpoints.

Adds, on the public ``/api/v1/dataset`` namespace, the endpoints the already
built fe-user client (``vbwd-fe-user/plugins/dataset/src/api/datasetApi.ts``)
calls:

  GET /api/v1/dataset/<slug>       public catalogue detail (no location/data leak)
  GET /api/v1/dataset/my           the caller's entitled datasets (session auth)
  GET /api/v1/dataset/categories   the dataset_category index

Also proves the static ``/my`` and ``/categories`` paths are NOT captured by the
``/<slug>`` converter (the route-ordering guard).

Exercises the real Flask app + PostgreSQL (rolled back per test).
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from flask import current_app

from vbwd.events.bus import event_bus
from vbwd.extensions import db as _db
from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User

from plugins.cms.src.models.cms_term import CmsTerm
from plugins.dataset import build_dataset_access_service
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.repositories.dataset_repository import DatasetRepository
from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from plugins.dataset.dataset.services.dataset_service import DatasetService
from plugins.dataset.dataset.services.dataset_taxonomy_service import (
    DATASET_CATEGORY_TERM_TYPE,
)
from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(db):
    user = User(
        id=uuid4(),
        email=f"user-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _catalogue_service():
    backend = LocalArchiveBackend(current_app.container.filesystem_manager())
    return DatasetService(
        dataset_repository=DatasetRepository(_db.session),
        snapshot_repository=DatasetSnapshotRepository(_db.session),
        storage_backend=backend,
        event_bus=event_bus,
    )


def _make_dataset_with_snapshot(db, *, slug=None, is_active=True):
    dataset = Dataset()
    dataset.slug = slug or f"air-quality-{uuid4().hex[:8]}"
    dataset.title = "Air Quality"
    dataset.description = "Daily air-quality index by city"
    dataset.source_attribution = "Public data pipeline"
    dataset.price = 100.0
    dataset.is_active = is_active
    db.session.add(dataset)
    db.session.commit()

    service = _catalogue_service()
    service.add_snapshot(
        str(dataset.id),
        data=b"city,aqi\nBerlin,42\n",
        ext="csv",
        taken_at="2026-06-01-00-00",
        category_slug="environment",
    )
    db.session.commit()
    return dataset


def _grant(db, user, dataset):
    build_dataset_access_service().grant(user.id, dataset.id, triggered_by="test")
    db.session.commit()


def _make_category(db, slug):
    term = CmsTerm()
    term.term_type = DATASET_CATEGORY_TERM_TYPE
    term.slug = slug
    term.name = slug.replace("-", " ").title()
    db.session.add(term)
    db.session.commit()
    return term


def _auth_as(monkeypatch, user):
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = user
    auth_service = MagicMock()
    auth_service.verify_token.return_value = str(user.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: auth_service)


# ----------------------------------------------------------------------
# Public catalogue detail
# ----------------------------------------------------------------------


def test_detail_returns_public_fields_without_location(db, client):
    dataset = _make_dataset_with_snapshot(db)

    response = client.get(f"/api/v1/dataset/{dataset.slug}")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["slug"] == dataset.slug
    assert payload["title"] == "Air Quality"
    assert payload["description"] == "Daily air-quality index by city"
    assert payload["source_attribution"] == "Public data pipeline"
    assert payload["price"] == 100.0
    # Never expose the raw storage path nor the data itself (GDPR/security).
    assert "location" not in payload
    assert "data" not in payload


def test_detail_unknown_slug_is_404(db, client):
    response = client.get(f"/api/v1/dataset/does-not-exist-{uuid4().hex[:6]}")
    assert response.status_code == 404


# ----------------------------------------------------------------------
# My datasets (entitled, session auth, GDPR: own entitlements only)
# ----------------------------------------------------------------------


def test_my_returns_only_the_callers_entitled_datasets(db, client, monkeypatch):
    user = _make_user(db)
    entitled = _make_dataset_with_snapshot(db)
    _make_dataset_with_snapshot(db)  # exists but NOT granted to this user
    _grant(db, user, entitled)
    _auth_as(monkeypatch, user)

    response = client.get(
        "/api/v1/dataset/my", headers={"Authorization": "Bearer valid"}
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert isinstance(payload, list)
    slugs = {item["slug"] for item in payload}
    assert slugs == {entitled.slug}


def test_my_requires_authentication(db, client):
    response = client.get("/api/v1/dataset/my")
    assert response.status_code == 401


# ----------------------------------------------------------------------
# Categories index
# ----------------------------------------------------------------------


def test_categories_returns_the_dataset_category_index(db, client):
    category = _make_category(db, f"environment-{uuid4().hex[:6]}")

    response = client.get("/api/v1/dataset/categories")

    assert response.status_code == 200
    categories = response.get_json()["categories"]
    slugs = {entry["slug"] for entry in categories}
    assert category.slug in slugs
    match = next(entry for entry in categories if entry["slug"] == category.slug)
    assert match["label"] == category.name


# ----------------------------------------------------------------------
# Route-ordering guard — static paths are not captured by /<slug>
# ----------------------------------------------------------------------


def test_static_paths_are_not_captured_by_the_slug_converter(db, client):
    # /categories hits the public categories handler (envelope), not a 404 slug
    # lookup.
    categories = client.get("/api/v1/dataset/categories")
    assert categories.status_code == 200
    assert "categories" in categories.get_json()

    # /my hits the @require_auth handler (401 when unauthenticated), NOT the
    # public /<slug> handler (which would 404 the unknown slug "my").
    my_datasets = client.get("/api/v1/dataset/my")
    assert my_datasets.status_code == 401


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

"""Entitled-user snapshot version listing + per-version download.

Exercises the real Flask app + PostgreSQL (rolled back per test):

* ``GET /api/v1/dataset/<slug>/snapshots`` lists the archived versions
  newest-first, flags the ``last`` one, and never leaks the raw storage
  ``location``;
* ``GET /api/v1/dataset/<slug>/snapshots/<snapshot_id>/download`` streams that
  specific version as an attachment;
* both accept EITHER a session JWT (dashboard) OR the ``X-API-Key`` scoped key
  (programmatic), and both are entitlement-gated (403 when not entitled, 404 on
  an unknown slug/snapshot).
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from flask import current_app

from vbwd.events.bus import event_bus
from vbwd.extensions import db as _db
from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User
from vbwd.repositories.api_key_repository import ApiKeyRepository
from vbwd.services.api_key_service import ApiKeyService

from plugins.dataset import build_dataset_access_service
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.repositories.dataset_repository import DatasetRepository
from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from plugins.dataset.dataset.services.dataset_service import DatasetService
from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend

READ_SCOPE = "dataset:read"


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


def _make_dataset(db, *, slug=None):
    dataset = Dataset()
    dataset.slug = slug or f"air-quality-{uuid4().hex[:8]}"
    dataset.title = "Air Quality"
    dataset.price = 100.0
    dataset.is_active = True
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _add_snapshot(db, dataset, *, taken_at, rows_csv):
    service = _catalogue_service()
    snapshot = service.add_snapshot(
        str(dataset.id),
        data=rows_csv,
        ext="csv",
        taken_at=taken_at,
        category_slug="environment",
    )
    db.session.commit()
    return snapshot


def _grant(db, user, dataset):
    build_dataset_access_service().grant(user.id, dataset.id, triggered_by="test")
    db.session.commit()


def _api_key(db, user, scopes=(READ_SCOPE,)):
    service = ApiKeyService(ApiKeyRepository(db.session))
    _, plaintext = service.generate(
        user_id=user.id, label="snapshot test", scopes=list(scopes)
    )
    db.session.commit()
    return plaintext


def _auth_as(monkeypatch, user):
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = user
    auth_service = MagicMock()
    auth_service.verify_token.return_value = str(user.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: auth_service)


def _dataset_with_three_snapshots(db):
    dataset = _make_dataset(db)
    _add_snapshot(db, dataset, taken_at="2026-06-01-00-00", rows_csv=b"a,b\n1,1\n")
    _add_snapshot(db, dataset, taken_at="2026-06-02-00-00", rows_csv=b"a,b\n2,2\n")
    latest = _add_snapshot(
        db, dataset, taken_at="2026-06-03-00-00", rows_csv=b"a,b\n3,3\n"
    )
    return dataset, latest


# ----------------------------------------------------------------------
# List versions
# ----------------------------------------------------------------------


def test_list_snapshots_session_newest_first_with_is_last(db, client, monkeypatch):
    user = _make_user(db)
    dataset, latest = _dataset_with_three_snapshots(db)
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["total"] == 3
    taken_ats = [snapshot["taken_at"] for snapshot in payload["snapshots"]]
    assert taken_ats == [
        "2026-06-03-00-00",
        "2026-06-02-00-00",
        "2026-06-01-00-00",
    ]
    newest = payload["snapshots"][0]
    assert newest["id"] == str(latest.id)
    assert newest["is_last"] is True
    assert all(item["is_last"] is False for item in payload["snapshots"][1:])
    # Never leak the raw storage location.
    assert all("location" not in item for item in payload["snapshots"])
    # The public per-version shape is present.
    for key in ("size_bytes", "ext", "checksum", "storage_backend"):
        assert key in newest


def test_list_snapshots_via_api_key(db, client):
    user = _make_user(db)
    dataset, _ = _dataset_with_three_snapshots(db)
    _grant(db, user, dataset)
    key = _api_key(db, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots", headers={"X-API-Key": key}
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["total"] == 3


def test_list_snapshots_forbidden_when_not_entitled(db, client, monkeypatch):
    user = _make_user(db)
    dataset, _ = _dataset_with_three_snapshots(db)  # no grant
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 403


def test_list_snapshots_unknown_slug_404(db, client, monkeypatch):
    user = _make_user(db)
    _auth_as(monkeypatch, user)

    response = client.get(
        "/api/v1/dataset/does-not-exist/snapshots",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 404


def test_list_snapshots_requires_auth(db, client):
    dataset, _ = _dataset_with_three_snapshots(db)
    response = client.get(f"/api/v1/dataset/{dataset.slug}/snapshots")
    assert response.status_code == 401


# ----------------------------------------------------------------------
# Download a specific version
# ----------------------------------------------------------------------


def test_download_specific_snapshot_streams_right_version(db, client, monkeypatch):
    user = _make_user(db)
    dataset = _make_dataset(db)
    older = _add_snapshot(
        db, dataset, taken_at="2026-06-01-00-00", rows_csv=b"old,data\n1,1\n"
    )
    _add_snapshot(db, dataset, taken_at="2026-06-02-00-00", rows_csv=b"new,data\n2,2\n")
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots/{older.id}/download",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    disposition = response.headers.get("Content-Disposition", "")
    assert disposition.startswith("attachment")
    assert dataset.slug in disposition
    # Streams the OLDER version's bytes, not the last snapshot.
    assert response.get_data() == b"old,data\n1,1\n"


def test_download_specific_snapshot_via_api_key(db, client):
    user = _make_user(db)
    dataset = _make_dataset(db)
    older = _add_snapshot(
        db, dataset, taken_at="2026-06-01-00-00", rows_csv=b"old,data\n1,1\n"
    )
    _add_snapshot(db, dataset, taken_at="2026-06-02-00-00", rows_csv=b"new,data\n2,2\n")
    _grant(db, user, dataset)
    key = _api_key(db, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots/{older.id}/download",
        headers={"X-API-Key": key},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_data() == b"old,data\n1,1\n"


def test_download_snapshot_forbidden_when_not_entitled(db, client, monkeypatch):
    user = _make_user(db)
    dataset, latest = _dataset_with_three_snapshots(db)  # no grant
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots/{latest.id}/download",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 403


def test_download_unknown_snapshot_404(db, client, monkeypatch):
    user = _make_user(db)
    dataset, _ = _dataset_with_three_snapshots(db)
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots/{uuid4()}/download",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 404


def test_download_snapshot_from_other_dataset_404(db, client, monkeypatch):
    user = _make_user(db)
    dataset, _ = _dataset_with_three_snapshots(db)
    other = _make_dataset(db)
    foreign = _add_snapshot(
        db, other, taken_at="2026-06-01-00-00", rows_csv=b"x,y\n1,1\n"
    )
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots/{foreign.id}/download",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 404


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

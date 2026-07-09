"""S124 follow-up — admin download of one issue file (NO entitlement gate).

An admin manages the catalogue but is not necessarily *entitled* to a dataset,
so the admin file-download route deliberately skips the entitlement check the
entitled ``/dataset/<slug>/...`` routes enforce. Behaviour otherwise mirrors the
entitled ``download_snapshot_file``: ``file_id == "primary"`` streams the primary
data file; any other id streams the resolved member with its own content type and
original filename.

Exercises the real Flask app + PostgreSQL (rolled back per test); admin auth is
faked in-process the same way as ``test_admin_snapshot_files``.
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from flask import current_app

from vbwd.events.bus import event_bus
from vbwd.extensions import db as _db
from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User

from plugins.dataset import build_snapshot_file_service
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.repositories.dataset_repository import DatasetRepository
from plugins.dataset.dataset.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from plugins.dataset.dataset.services.dataset_service import DatasetService
from plugins.dataset.dataset.services.storage.local_backend import LocalArchiveBackend

HEADERS = {"Authorization": "Bearer valid"}


@pytest.fixture
def client(app):
    return app.test_client()


def _make_admin(db):
    admin = User(
        id=uuid4(),
        email=f"admin-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
    )
    db.session.add(admin)
    db.session.commit()
    return admin


def _auth_as_admin(monkeypatch, admin):
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = admin
    auth_service = MagicMock()
    auth_service.verify_token.return_value = str(admin.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: auth_service)
    monkeypatch.setattr(type(admin), "is_admin", property(lambda self: True))
    monkeypatch.setattr(type(admin), "has_permission", lambda self, perm: True)


def _catalogue_service():
    backend = LocalArchiveBackend(current_app.container.filesystem_manager())
    return DatasetService(
        dataset_repository=DatasetRepository(_db.session),
        snapshot_repository=DatasetSnapshotRepository(_db.session),
        storage_backend=backend,
        event_bus=event_bus,
    )


def _make_issue_with_files(db):
    dataset = Dataset()
    dataset.slug = f"air-quality-{uuid4().hex[:8]}"
    dataset.title = "Air Quality"
    dataset.price = 100.0
    dataset.is_active = True
    db.session.add(dataset)
    db.session.commit()

    snapshot = _catalogue_service().add_snapshot(
        str(dataset.id),
        data=b"city,aqi\nBerlin,42\n",
        ext="csv",
        taken_at="2026-06-01-00-00",
        category_slug="environment",
    )
    db.session.commit()

    member = build_snapshot_file_service().add_file(
        str(dataset.id), str(snapshot.id), "report.pdf", "document", b"%PDF report\n"
    )
    db.session.commit()
    return dataset, snapshot, member


def _download_url(dataset_id, snapshot_id, file_id):
    return (
        f"/api/v1/admin/datasets/{dataset_id}"
        f"/snapshots/{snapshot_id}/files/{file_id}/download"
    )


def test_admin_downloads_primary_data_file(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset, snapshot, _member = _make_issue_with_files(db)

    response = client.get(
        _download_url(dataset.id, snapshot.id, "primary"), headers=HEADERS
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_data() == b"city,aqi\nBerlin,42\n"
    assert response.headers["Content-Type"].startswith("text/csv")
    disposition = response.headers.get("Content-Disposition", "")
    assert disposition.startswith("attachment")
    assert f"{dataset.slug}-2026-06-01-00-00.csv" in disposition


def test_admin_downloads_member_while_not_entitled(db, client, monkeypatch):
    """The whole point: the admin is NOT granted the dataset yet downloads the PDF."""
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset, snapshot, member = _make_issue_with_files(db)

    response = client.get(
        _download_url(dataset.id, snapshot.id, member.id), headers=HEADERS
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_data() == b"%PDF report\n"
    assert response.headers["Content-Type"].startswith("application/pdf")
    assert "report.pdf" in response.headers.get("Content-Disposition", "")


def test_unknown_dataset_is_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    response = client.get(_download_url(uuid4(), uuid4(), "primary"), headers=HEADERS)
    assert response.status_code == 404


def test_unknown_snapshot_is_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset, _snapshot, _member = _make_issue_with_files(db)

    response = client.get(
        _download_url(dataset.id, uuid4(), "primary"), headers=HEADERS
    )
    assert response.status_code == 404


def test_unknown_member_is_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset, snapshot, _member = _make_issue_with_files(db)

    response = client.get(
        _download_url(dataset.id, snapshot.id, uuid4()), headers=HEADERS
    )
    assert response.status_code == 404


def test_member_of_a_different_snapshot_is_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset, snapshot, member = _make_issue_with_files(db)
    other_snapshot = _catalogue_service().add_snapshot(
        str(dataset.id),
        data=b"city,aqi\nHamburg,7\n",
        ext="csv",
        taken_at="2026-07-01-00-00",
        category_slug="environment",
    )
    db.session.commit()

    response = client.get(
        _download_url(dataset.id, other_snapshot.id, member.id), headers=HEADERS
    )
    assert response.status_code == 404


def test_non_admin_is_rejected(db, client):
    response = client.get(
        _download_url(uuid4(), uuid4(), "primary"),
        headers={"Authorization": "Bearer nope"},
    )
    assert response.status_code in (401, 403)


def test_admin_download_route_is_not_shadowed(app):
    """The admin ``/files/<file_id>/download`` resolves to its own handler."""
    adapter = app.url_map.bind("localhost")
    download_endpoint, _ = adapter.match(
        "/api/v1/admin/datasets/d1/snapshots/s1/files/primary/download",
        method="GET",
    )
    delete_endpoint, _ = adapter.match(
        "/api/v1/admin/datasets/d1/snapshots/s1/files/f1",
        method="DELETE",
    )
    assert download_endpoint == "dataset.admin_download_snapshot_file"
    assert delete_endpoint == "dataset.admin_delete_snapshot_file"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

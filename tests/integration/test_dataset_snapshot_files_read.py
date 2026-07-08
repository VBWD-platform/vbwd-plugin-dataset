"""S124 — entitled issue-file list / download / archive (dual auth, not metered).

Mirrors the ``/snapshots/<id>/download`` gate: dual auth (session JWT or scoped
API key) + entitlement. Exercises the real Flask app + PostgreSQL (rolled back
per test).
"""
import io
import zipfile
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from flask import current_app

from vbwd.events.bus import event_bus
from vbwd.extensions import db as _db
from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User

from plugins.dataset import build_dataset_access_service, build_snapshot_file_service
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


def _make_user(db, role=UserRole.USER):
    user = User(
        id=uuid4(),
        email=f"user-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=role,
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

    file_service = build_snapshot_file_service()
    file_service.add_file(
        str(dataset.id), str(snapshot.id), "report.pdf", "document", b"%PDF report\n"
    )
    db.session.commit()
    return dataset, snapshot


def _grant(db, user, dataset):
    build_dataset_access_service().grant(user.id, dataset.id, triggered_by="test")
    db.session.commit()


def _auth_as(monkeypatch, user):
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = user
    auth_service = MagicMock()
    auth_service.verify_token.return_value = str(user.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: auth_service)


def _files_url(slug, snapshot_id):
    return f"/api/v1/dataset/{slug}/snapshots/{snapshot_id}/files"


def test_entitled_list_returns_primary_and_member_with_download_urls(
    db, client, monkeypatch
):
    user = _make_user(db)
    dataset, snapshot = _make_issue_with_files(db)
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        _files_url(dataset.slug, snapshot.id),
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200, response.get_json()
    files = response.get_json()["files"]
    assert files[0]["id"] == "primary"
    assert files[1]["role"] == "document"
    assert all("download_url" in entry for entry in files)
    assert files[0]["download_url"].endswith("/files/primary/download")


def test_unentitled_list_is_403(db, client, monkeypatch):
    user = _make_user(db)
    dataset, snapshot = _make_issue_with_files(db)  # no grant
    _auth_as(monkeypatch, user)

    response = client.get(
        _files_url(dataset.slug, snapshot.id),
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 403


def test_download_primary_serves_the_data_file(db, client, monkeypatch):
    user = _make_user(db)
    dataset, snapshot = _make_issue_with_files(db)
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots/{snapshot.id}/files/primary/download",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200
    assert response.get_data() == b"city,aqi\nBerlin,42\n"
    assert response.headers.get("Content-Disposition", "").startswith("attachment")


def test_download_member_uses_content_type_and_original_filename(
    db, client, monkeypatch
):
    user = _make_user(db)
    dataset, snapshot = _make_issue_with_files(db)
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    listing = client.get(
        _files_url(dataset.slug, snapshot.id),
        headers={"Authorization": "Bearer valid"},
    ).get_json()["files"]
    member_id = listing[1]["id"]

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots/{snapshot.id}/files/{member_id}/download",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200
    assert response.get_data() == b"%PDF report\n"
    assert response.headers["Content-Type"].startswith("application/pdf")
    assert "report.pdf" in response.headers.get("Content-Disposition", "")


def test_download_unknown_member_is_404(db, client, monkeypatch):
    user = _make_user(db)
    dataset, snapshot = _make_issue_with_files(db)
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots/{snapshot.id}/files/{uuid4()}/download",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 404


def test_archive_contains_primary_and_member(db, client, monkeypatch):
    user = _make_user(db)
    dataset, snapshot = _make_issue_with_files(db)
    _grant(db, user, dataset)
    _auth_as(monkeypatch, user)

    response = client.get(
        f"/api/v1/dataset/{dataset.slug}/snapshots/{snapshot.id}/archive",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
        names = set(archive.namelist())
        assert f"{dataset.slug}-2026-06-01-00-00.csv" in names
        assert "report.pdf" in names


def test_files_list_route_is_not_shadowed_by_file_id(app):
    """The static ``/files`` list resolves to its own handler, never a ``<file_id>``.

    Guards the route-ordering rule: the list rule is declared before any
    ``/files/<file_id>...`` rule so no id can shadow it.
    """
    adapter = app.url_map.bind("localhost")
    list_endpoint, _ = adapter.match(
        "/api/v1/dataset/air-quality/snapshots/abc/files", method="GET"
    )
    download_endpoint, _ = adapter.match(
        "/api/v1/dataset/air-quality/snapshots/abc/files/primary/download",
        method="GET",
    )
    assert list_endpoint == "dataset.list_snapshot_files"
    assert download_endpoint == "dataset.download_snapshot_file"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

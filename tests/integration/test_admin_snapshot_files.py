"""S124 — admin attach / list / delete companion files on an issue.

Exercises the real Flask app + PostgreSQL (rolled back per test); auth is faked
in-process the same way as ``test_admin_snapshot_rows`` (the booking pattern).
"""
import io
from uuid import uuid4

import pytest
from unittest.mock import MagicMock

from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User

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


def _seed_issue(client):
    slug = f"files-{uuid4().hex[:8]}"
    dataset_id = client.post(
        "/api/v1/admin/datasets",
        json={"title": "Files", "slug": slug, "price": 100.0},
        headers=HEADERS,
    ).get_json()["id"]
    snapshot_id = client.post(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots",
        json={"content": "a,b\n1,2\n", "ext": "csv", "taken_at": "2026-05-01-00-00"},
        headers=HEADERS,
    ).get_json()["id"]
    return dataset_id, snapshot_id


def _files_url(dataset_id, snapshot_id, file_id=None):
    base = f"/api/v1/admin/datasets/{dataset_id}/snapshots/{snapshot_id}/files"
    return f"{base}/{file_id}" if file_id else base


def _attach(client, dataset_id, snapshot_id, *, filename, role, data=b"%PDF\n"):
    return client.post(
        _files_url(dataset_id, snapshot_id),
        data={"file": (io.BytesIO(data), filename), "role": role},
        content_type="multipart/form-data",
        headers=HEADERS,
    )


def test_attach_returns_201_file_dict(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_issue(client)

    response = _attach(
        client, dataset_id, snapshot_id, filename="report.pdf", role="document"
    )
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["role"] == "document"
    assert body["filename"] == "report.pdf"
    assert "location" not in body


def test_list_includes_primary_then_member(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_issue(client)
    _attach(client, dataset_id, snapshot_id, filename="chart.png", role="chart")

    response = client.get(_files_url(dataset_id, snapshot_id), headers=HEADERS)
    assert response.status_code == 200
    files = response.get_json()["files"]
    assert files[0]["id"] == "primary"
    assert files[0]["role"] == "data"
    assert files[1]["role"] == "chart"


def test_delete_removes_the_member(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_issue(client)
    file_id = _attach(
        client, dataset_id, snapshot_id, filename="a.pdf", role="document"
    ).get_json()["id"]

    deleted = client.delete(
        _files_url(dataset_id, snapshot_id, file_id), headers=HEADERS
    )
    assert deleted.status_code == 200
    remaining = client.get(_files_url(dataset_id, snapshot_id), headers=HEADERS)
    assert [entry["id"] for entry in remaining.get_json()["files"]] == ["primary"]


def test_attach_bad_role_is_400(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_issue(client)

    response = _attach(client, dataset_id, snapshot_id, filename="a.pdf", role="banner")
    assert response.status_code == 400


def test_attach_disallowed_extension_is_400(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_issue(client)

    response = _attach(
        client, dataset_id, snapshot_id, filename="evil.exe", role="other"
    )
    assert response.status_code == 400


def test_attach_oversize_is_400(db, client, monkeypatch):
    import plugins.dataset as dataset_pkg

    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_issue(client)
    monkeypatch.setitem(dataset_pkg.DEFAULT_CONFIG, "max_file_size_bytes", 3)

    response = _attach(
        client,
        dataset_id,
        snapshot_id,
        filename="a.pdf",
        role="document",
        data=b"toolong",
    )
    assert response.status_code == 400


def test_attach_to_wrong_dataset_is_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    response = _attach(client, uuid4(), uuid4(), filename="a.pdf", role="document")
    assert response.status_code == 404


def test_files_requires_admin_auth(db, client):
    response = client.get(
        _files_url(uuid4(), uuid4()), headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code in (401, 403)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

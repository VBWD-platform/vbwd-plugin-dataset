"""Admin paginated snapshot-rows endpoint.

``GET /api/v1/admin/datasets/<id>/snapshots/<sid>/rows?offset=&limit=`` returns
one server-paginated page ``{columns, rows, offset, limit, has_more}`` for a
specific snapshot. Same admin auth stack as the other ``/admin/datasets/...``
routes; NOT entitlement-gated. ``limit`` is clamped to a server max.

Exercises the real Flask app + PostgreSQL (rolled back per test); auth is faked
in-process the same way as ``test_admin_datasets`` (the booking-plugin pattern).
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

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


def _create_dataset(client, *, title, slug):
    return client.post(
        "/api/v1/admin/datasets",
        json={"title": title, "slug": slug, "price": 100.0},
        headers=HEADERS,
    )


def _upload_snapshot(client, dataset_id, *, content, taken_at="2026-05-01-00-00"):
    return client.post(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots",
        json={"content": content, "ext": "csv", "taken_at": taken_at},
        headers=HEADERS,
    )


def _seed_dataset_with_rows(client, row_count):
    slug = f"rows-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="Rows", slug=slug).get_json()["id"]
    body = "\n".join(f"{index},{index * 2}" for index in range(row_count))
    snapshot_id = _upload_snapshot(
        client, dataset_id, content=f"col_a,col_b\n{body}\n"
    ).get_json()["id"]
    return dataset_id, snapshot_id


def _rows_url(dataset_id, snapshot_id, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    base = f"/api/v1/admin/datasets/{dataset_id}/snapshots/{snapshot_id}/rows"
    return f"{base}?{query}" if query else base


def test_first_page_returns_columns_rows_and_has_more(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_dataset_with_rows(client, 10)

    response = client.get(
        _rows_url(dataset_id, snapshot_id, offset=0, limit=3), headers=HEADERS
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["columns"] == ["col_a", "col_b"]
    assert body["rows"] == [["0", "0"], ["1", "2"], ["2", "4"]]
    assert body["offset"] == 0
    assert body["limit"] == 3
    assert body["has_more"] is True


def test_second_page_via_offset(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_dataset_with_rows(client, 10)

    response = client.get(
        _rows_url(dataset_id, snapshot_id, offset=3, limit=3), headers=HEADERS
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["rows"] == [["3", "6"], ["4", "8"], ["5", "10"]]
    assert body["offset"] == 3
    assert body["has_more"] is True


def test_has_more_false_on_the_last_page(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_dataset_with_rows(client, 5)

    response = client.get(
        _rows_url(dataset_id, snapshot_id, offset=3, limit=3), headers=HEADERS
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["rows"] == [["3", "6"], ["4", "8"]]
    assert body["has_more"] is False


def test_limit_is_clamped_to_the_server_max(db, client, monkeypatch):
    import plugins.dataset.dataset.routes as routes_mod

    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    monkeypatch.setattr(routes_mod, "PAGE_MAX_ROWS", 5)
    dataset_id, snapshot_id = _seed_dataset_with_rows(client, 20)

    response = client.get(
        _rows_url(dataset_id, snapshot_id, offset=0, limit=999), headers=HEADERS
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["limit"] == 5
    assert len(body["rows"]) == 5


def test_garbage_offset_and_limit_fall_back_defensively(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    dataset_id, snapshot_id = _seed_dataset_with_rows(client, 10)

    response = client.get(
        _rows_url(dataset_id, snapshot_id, offset="abc", limit="xyz"), headers=HEADERS
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["offset"] == 0
    assert body["limit"] == 100  # PREVIEW_MAX_ROWS default


def test_unknown_dataset_returns_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    response = client.get(_rows_url(uuid4(), uuid4()), headers=HEADERS)
    assert response.status_code == 404
    assert response.get_json()["error"] == "Not found"


def test_unknown_snapshot_returns_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    slug = f"nosnap-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="NoSnap", slug=slug).get_json()["id"]

    response = client.get(_rows_url(dataset_id, uuid4()), headers=HEADERS)
    assert response.status_code == 404
    assert response.get_json()["error"] == "Snapshot not found"


def test_rows_requires_admin_auth(db, client, monkeypatch):
    response = client.get(
        _rows_url(uuid4(), uuid4()), headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code in (401, 403)

"""Admin spreadsheet preview endpoint.

``GET /api/v1/admin/datasets/<id>/preview`` returns the dataset's data as a
generic ``{"columns", "rows"}`` structure (reusing the shared preview builder),
defaulting to the dataset's last snapshot and honouring an explicit
``?snapshot_id=``. Unlike the public preview it is NOT entitlement-gated — an
admin can always preview — but it carries the same admin auth + permission
stack as the other ``/admin/datasets/...`` routes.

Exercises the real Flask app + PostgreSQL (rolled back per test); auth is faked
in-process the same way as ``test_admin_datasets`` (the booking-plugin pattern).
"""
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
    from unittest.mock import MagicMock

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


def _upload_snapshot(client, dataset_id, *, content, taken_at):
    return client.post(
        f"/api/v1/admin/datasets/{dataset_id}/snapshots",
        json={"content": content, "ext": "csv", "taken_at": taken_at},
        headers=HEADERS,
    )


def test_preview_returns_columns_and_rows_from_last_snapshot(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"prev-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="Preview", slug=slug).get_json()["id"]
    _upload_snapshot(
        client,
        dataset_id,
        content="city,aqi\nBerlin,42\nParis,17\n",
        taken_at="2026-05-01-00-00",
    )

    response = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/preview", headers=HEADERS
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["columns"] == ["city", "aqi"]
    assert body["rows"] == [["Berlin", "42"], ["Paris", "17"]]


def test_preview_defaults_to_last_snapshot_after_repoint(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"prevlast-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="PrevLast", slug=slug).get_json()["id"]
    _upload_snapshot(
        client, dataset_id, content="a\nold\n", taken_at="2026-04-01-00-00"
    )
    second = _upload_snapshot(
        client, dataset_id, content="a\nnew\n", taken_at="2026-05-01-00-00"
    ).get_json()

    response = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/preview", headers=HEADERS
    )
    assert response.status_code == 200
    # ``last`` is the second upload, so the default preview reflects it.
    assert response.get_json()["rows"] == [["new"]]
    assert second["taken_at"] == "2026-05-01-00-00"


def test_preview_honours_explicit_snapshot_id(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"prevpin-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="PrevPin", slug=slug).get_json()["id"]
    first = _upload_snapshot(
        client, dataset_id, content="a\nold\n", taken_at="2026-04-01-00-00"
    ).get_json()
    _upload_snapshot(
        client, dataset_id, content="a\nnew\n", taken_at="2026-05-01-00-00"
    )

    response = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/preview?snapshot_id={first['id']}",
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.get_json()["rows"] == [["old"]]


def test_preview_respects_row_cap(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"prevcap-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="PrevCap", slug=slug).get_json()["id"]
    body_rows = "\n".join(f"{index}" for index in range(500))
    _upload_snapshot(
        client, dataset_id, content=f"n\n{body_rows}\n", taken_at="2026-05-01-00-00"
    )

    import plugins.dataset.dataset.routes as routes_mod

    monkeypatch.setattr(routes_mod, "PREVIEW_MAX_ROWS", 5)
    response = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/preview", headers=HEADERS
    )
    assert response.status_code == 200
    assert len(response.get_json()["rows"]) == 5


def test_preview_unknown_dataset_returns_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    response = client.get(f"/api/v1/admin/datasets/{uuid4()}/preview", headers=HEADERS)
    assert response.status_code == 404
    assert response.get_json()["error"] == "Not found"


def test_preview_unknown_snapshot_returns_404(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"prevbad-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="PrevBad", slug=slug).get_json()["id"]

    response = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/preview?snapshot_id={uuid4()}",
        headers=HEADERS,
    )
    assert response.status_code == 404
    assert response.get_json()["error"] == "Snapshot not found"


def test_preview_without_snapshots_returns_empty_but_200(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    slug = f"prevempty-{uuid4().hex[:8]}"
    dataset_id = _create_dataset(client, title="PrevEmpty", slug=slug).get_json()["id"]

    response = client.get(
        f"/api/v1/admin/datasets/{dataset_id}/preview", headers=HEADERS
    )
    assert response.status_code == 200
    assert response.get_json() == {"columns": [], "rows": []}


def test_preview_requires_admin_auth(db, client, monkeypatch):
    slug = f"prevauth-{uuid4().hex[:8]}"
    # No auth patched — the admin stack must reject an unauthenticated caller.
    response = client.get(
        f"/api/v1/admin/datasets/{uuid4()}/preview",
        headers={"Authorization": "Bearer nope"},
    )
    assert response.status_code in (401, 403)
    assert slug  # keep slug referenced for symmetry with other tests

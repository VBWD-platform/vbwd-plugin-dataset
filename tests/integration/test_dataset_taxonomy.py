"""T4 — dataset<->dataset_category junction + the admin ``?category=`` filter.

Exercises the real Flask app + PostgreSQL (rolled-back per test). Categories are
the shared cms ``dataset_category`` terms; the NET-NEW ``dataset_term`` junction
links a dataset to one, and the admin list ``?category=`` filter resolves through
it (by slug or by id).
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User

from plugins.cms.src.models.cms_term import CmsTerm
from plugins.dataset.dataset.services.dataset_taxonomy_service import (
    DATASET_CATEGORY_TERM_TYPE,
)

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


def _make_category(db, slug):
    term = CmsTerm()
    term.term_type = DATASET_CATEGORY_TERM_TYPE
    term.slug = slug
    term.name = slug.replace("-", " ").title()
    db.session.add(term)
    db.session.commit()
    return term


def _create_dataset(client, *, title, slug):
    resp = client.post(
        "/api/v1/admin/datasets",
        json={"title": title, "slug": slug, "price": 100.0},
        headers=HEADERS,
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["id"]


def _list_slugs(client, query):
    resp = client.get(f"/api/v1/admin/datasets?{query}", headers=HEADERS)
    assert resp.status_code == 200, resp.get_json()
    return {item["slug"] for item in resp.get_json()["items"]}


def test_assign_category_then_filter_by_slug_and_id(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    category = _make_category(db, f"environment-{uuid4().hex[:6]}")
    inside_slug = f"air-{uuid4().hex[:6]}"
    outside_slug = f"noise-{uuid4().hex[:6]}"
    inside_id = _create_dataset(client, title="Air", slug=inside_slug)
    _create_dataset(client, title="Noise", slug=outside_slug)

    assign = client.post(
        f"/api/v1/admin/datasets/{inside_id}/categories",
        json={"term_id": str(category.id)},
        headers=HEADERS,
    )
    assert assign.status_code == 200, assign.get_json()

    # The read counterpart lists the assigned category term.
    listed = client.get(
        f"/api/v1/admin/datasets/{inside_id}/categories", headers=HEADERS
    )
    assert listed.status_code == 200
    assert str(category.id) in listed.get_json()["term_ids"]

    by_slug = _list_slugs(client, f"category={category.slug}")
    assert inside_slug in by_slug
    assert outside_slug not in by_slug

    by_id = _list_slugs(client, f"category={category.id}")
    assert inside_slug in by_id
    assert outside_slug not in by_id


def test_unassign_category_removes_from_filter(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    category = _make_category(db, f"env-{uuid4().hex[:6]}")
    dataset_slug = f"ds-{uuid4().hex[:6]}"
    dataset_id = _create_dataset(client, title="DS", slug=dataset_slug)

    client.post(
        f"/api/v1/admin/datasets/{dataset_id}/categories",
        json={"term_id": str(category.id)},
        headers=HEADERS,
    )
    assert dataset_slug in _list_slugs(client, f"category={category.slug}")

    unassign = client.delete(
        f"/api/v1/admin/datasets/{dataset_id}/categories/{category.id}",
        headers=HEADERS,
    )
    assert unassign.status_code == 200, unassign.get_json()
    assert dataset_slug not in _list_slugs(client, f"category={category.slug}")


def test_bulk_assign_category_assigns_many(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    category = _make_category(db, f"bulk-{uuid4().hex[:6]}")
    slugs = [f"bulk-ds-{index}-{uuid4().hex[:6]}" for index in range(3)]
    ids = [
        _create_dataset(client, title=f"B{index}", slug=slug)
        for index, slug in enumerate(slugs)
    ]

    bulk = client.post(
        "/api/v1/admin/datasets/bulk-assign-category",
        json={"dataset_ids": ids, "term_id": str(category.id)},
        headers=HEADERS,
    )
    assert bulk.status_code == 200, bulk.get_json()
    assert bulk.get_json()["assigned"] == 3

    filtered = _list_slugs(client, f"category={category.slug}&per_page=100")
    for slug in slugs:
        assert slug in filtered


def test_unknown_category_still_returns_empty(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)

    _create_dataset(client, title="Any", slug=f"any-{uuid4().hex[:6]}")
    resp = client.get("/api/v1/admin/datasets?category=does-not-exist", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["items"] == []

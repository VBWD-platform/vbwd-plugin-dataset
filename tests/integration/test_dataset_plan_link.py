"""BUG 2 regression — the dataset serializer must expose ``tariff_plan_id``.

The admin editor's plan select and the fe-user "Get dataset" CTA both read
``tariff_plan_id`` off the dataset dict, but ``Dataset.to_dict`` used to omit it,
so the linked plan never round-tripped. This asserts the admin create/update
accept + persist the link, the admin GET round-trips it, and the public
``/api/v1/dataset/<slug>`` detail exposes it too.
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


def _make_plan(db):
    from plugins.subscription.subscription.models.tarif_plan import (
        BillingPeriod,
        TarifPlan,
    )

    plan = TarifPlan(
        id=uuid4(),
        name="Data Access",
        slug=f"plan-{uuid4().hex[:8]}",
        price=19.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def test_create_persists_and_serializes_tariff_plan_id(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    plan = _make_plan(db)

    slug = f"linked-{uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/admin/datasets",
        json={
            "title": "Linked",
            "slug": slug,
            "price": 19.0,
            "tariff_plan_id": str(plan.id),
        },
        headers=HEADERS,
    )
    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    assert body["tariff_plan_id"] == str(plan.id)
    dataset_id = body["id"]

    # Admin GET round-trips the link.
    read = client.get(f"/api/v1/admin/datasets/{dataset_id}", headers=HEADERS)
    assert read.get_json()["tariff_plan_id"] == str(plan.id)

    # Public catalogue detail exposes it too (drives the fe "Get dataset" CTA).
    public = client.get(f"/api/v1/dataset/{slug}")
    assert public.status_code == 200
    assert public.get_json()["tariff_plan_id"] == str(plan.id)


def test_update_repoints_and_clears_tariff_plan_id(db, client, monkeypatch):
    admin = _make_admin(db)
    _auth_as_admin(monkeypatch, admin)
    plan_a = _make_plan(db)
    plan_b = _make_plan(db)

    slug = f"repoint-{uuid4().hex[:8]}"
    dataset_id = client.post(
        "/api/v1/admin/datasets",
        json={"title": "Repoint", "slug": slug, "tariff_plan_id": str(plan_a.id)},
        headers=HEADERS,
    ).get_json()["id"]

    # Repoint to plan B.
    repointed = client.put(
        f"/api/v1/admin/datasets/{dataset_id}",
        json={"tariff_plan_id": str(plan_b.id)},
        headers=HEADERS,
    )
    assert repointed.get_json()["tariff_plan_id"] == str(plan_b.id)

    # Clearing the link (null) drops it.
    cleared = client.put(
        f"/api/v1/admin/datasets/{dataset_id}",
        json={"tariff_plan_id": None},
        headers=HEADERS,
    )
    assert cleared.get_json()["tariff_plan_id"] is None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

"""BUG 4 regression — the one-time order produces a clickable dataset invoice
line AND grants entitlement on ``invoice.paid``.

The only prior buy path was the recurring subscription checkout (a SUBSCRIPTION
line the host links to the plan page, never reaching the dataset access page).
This drives the one-time differentiator end to end:

* ``POST /api/v1/dataset/orders`` creates an invoice whose only line is a
  ``LineItemType.CUSTOM`` line tagged ``plugin='dataset'`` + ``dataset_slug``
  (so the fe-user invoice detail resolves it to /dashboard/datasets/<slug>);
* the invoice carries ``payment_metadata.dataset`` (the shape the one-time
  handler reads);
* a zero-price order captures immediately → ``invoice.paid`` → the one-time
  handler grants an ACTIVE ``DatasetMembership`` → ``/api/v1/dataset/my`` lists
  it.
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from vbwd.extensions import db as _db
from vbwd.models.enums import InvoiceStatus, LineItemType, UserRole, UserStatus
from vbwd.models.invoice import UserInvoice
from vbwd.models.user import User

from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.models.dataset_membership import DatasetMembershipStatus
from plugins.dataset.dataset.repositories.dataset_membership_repository import (
    DatasetMembershipRepository,
)


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(db):
    user = User(
        id=uuid4(),
        email=f"buyer-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _auth_as(monkeypatch, user):
    import vbwd.middleware.auth as auth_mod

    repo = MagicMock()
    repo.find_by_id.return_value = user
    auth_service = MagicMock()
    auth_service.verify_token.return_value = str(user.id)
    monkeypatch.setattr(auth_mod, "UserRepository", lambda *a, **k: repo)
    monkeypatch.setattr(auth_mod, "AuthService", lambda *a, **k: auth_service)


def _make_free_dataset(db, slug=None):
    """A zero-price dataset so the order captures immediately (no payment step)."""
    dataset = Dataset()
    dataset.slug = slug or f"free-air-{uuid4().hex[:8]}"
    dataset.title = "Free Air Quality"
    dataset.price = 0.0
    dataset.is_active = True
    db.session.add(dataset)
    db.session.commit()
    return dataset


HEADERS = {"Authorization": "Bearer valid"}


def test_one_time_order_creates_custom_line_and_grants_access(db, client, monkeypatch):
    user = _make_user(db)
    _auth_as(monkeypatch, user)
    dataset = _make_free_dataset(db)

    order = client.post(
        "/api/v1/dataset/orders",
        json={"dataset_slug": dataset.slug},
        headers=HEADERS,
    )
    assert order.status_code == 201, order.get_json()
    invoice_id = order.get_json()["invoice_id"]

    # The invoice carries exactly one CUSTOM dataset line + the metadata shape
    # the one-time handler reads.
    invoice = _db.session.query(UserInvoice).filter_by(id=invoice_id).first()
    assert invoice is not None
    assert invoice.payment_metadata["dataset"]["dataset_id"] == str(dataset.id)
    line_items = list(invoice.line_items)
    assert len(line_items) == 1
    line = line_items[0]
    assert line.item_type == LineItemType.CUSTOM
    assert line.extra_data["plugin"] == "dataset"
    assert line.extra_data["dataset_slug"] == dataset.slug

    # Zero-price → captured → invoice.paid → access granted.
    assert invoice.status == InvoiceStatus.PAID
    membership = DatasetMembershipRepository(_db.session).find_by_user_and_dataset(
        user.id, dataset.id
    )
    assert membership is not None
    assert membership.status == DatasetMembershipStatus.ACTIVE.value

    # /my lists the freshly entitled dataset.
    mine = client.get("/api/v1/dataset/my", headers=HEADERS)
    assert mine.status_code == 200
    assert dataset.slug in {row["slug"] for row in mine.get_json()}


def test_one_time_order_unknown_dataset_returns_404(db, client, monkeypatch):
    user = _make_user(db)
    _auth_as(monkeypatch, user)

    missing = client.post(
        "/api/v1/dataset/orders",
        json={"dataset_slug": f"nope-{uuid4().hex[:8]}"},
        headers=HEADERS,
    )
    assert missing.status_code == 404


def test_one_time_order_requires_slug(db, client, monkeypatch):
    user = _make_user(db)
    _auth_as(monkeypatch, user)

    bad = client.post("/api/v1/dataset/orders", json={}, headers=HEADERS)
    assert bad.status_code == 400


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

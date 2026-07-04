"""One-time dataset order stamps the selling vendor onto the invoice line.

The money path is decoupled: the order service stamps the buyer invoice line's
``extra_data`` with the LOCAL ``vendor_id`` key under the selling vendor's user
id, and the central ``marketplace`` plugin credits the vendor on ``invoice.paid``
— dataset never imports marketplace. The stamp only happens for a vendor-owned
dataset AND only when vendor-mode is enabled (merged, never clobbers other keys).
"""
from uuid import uuid4

from flask import current_app

from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.services import dataset_order_service as order_module
from plugins.dataset.dataset.services.dataset_order_service import DatasetOrderService


def _make_user_id(db):
    """Create a real ``vbwd_user`` row so FK columns (vendor_id, user_id) resolve."""
    from vbwd.models.enums import UserRole, UserStatus
    from vbwd.models.user import User

    user = User(
        id=uuid4(),
        email=f"attr-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    db.session.add(user)
    db.session.flush()
    return user.id


def _make_dataset(db, vendor_id=None):
    dataset = Dataset()
    dataset.slug = f"attr-{uuid4().hex[:8]}"
    dataset.title = "Attribution Data"
    dataset.price = 0.0
    dataset.is_active = True
    dataset.vendor_id = vendor_id
    db.session.add(dataset)
    db.session.flush()
    return dataset


def _order_service(db):
    return DatasetOrderService(
        db.session, price_factory=current_app.container.price_factory()
    )


def test_line_stamps_vendor_id_when_owned_and_enabled(db, monkeypatch):
    monkeypatch.setattr(order_module, "marketplace_enabled", lambda: True)
    vendor_id = _make_user_id(db)
    dataset = _make_dataset(db, vendor_id=vendor_id)

    invoice = _order_service(db).create_one_time_order(_make_user_id(db), dataset)

    line = list(invoice.line_items)[0]
    assert line.extra_data["vendor_id"] == str(vendor_id)
    # Merged, never clobbers the plugin tag.
    assert line.extra_data["plugin"] == "dataset"


def test_line_not_stamped_when_marketplace_disabled(db, monkeypatch):
    monkeypatch.setattr(order_module, "marketplace_enabled", lambda: False)
    dataset = _make_dataset(db, vendor_id=_make_user_id(db))

    invoice = _order_service(db).create_one_time_order(_make_user_id(db), dataset)

    line = list(invoice.line_items)[0]
    assert "vendor_id" not in line.extra_data


def test_line_not_stamped_when_platform_owned(db, monkeypatch):
    monkeypatch.setattr(order_module, "marketplace_enabled", lambda: True)
    dataset = _make_dataset(db, vendor_id=None)

    invoice = _order_service(db).create_one_time_order(_make_user_id(db), dataset)

    line = list(invoice.line_items)[0]
    assert "vendor_id" not in line.extra_data

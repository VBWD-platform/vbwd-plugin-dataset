"""Regression (S110 T15) — a one-time dataset order captured through the REAL
payment path must durably grant dataset access.

Unlike ``test_dataset_access_lifecycle`` (which publishes onto an isolated bus
and ``commit()``s for the handler), this test exercises the exact chain a live
capture uses:

    DatasetOrderService.create_one_time_order
      → emit_payment_captured (PaymentCapturedEvent)
        → core PaymentCapturedHandler (marks PAID, processes line items)
          → event_bus.publish("invoice.paid", ...)
            → DatasetOneTimePaymentHandler.on_invoice_paid → grant

It never hand-rolls ``on_invoice_paid``.

The bug it guards: the dataset access repos only ``flush()``ed, so the grant
fired inside the capture request's context — whose scoped session is *rolled
back* at teardown — and was lost unless some unrelated downstream subscriber
happened to commit the shared session. The final assertion rolls the request
session back (the teardown analogue) and proves the membership survives, i.e.
the grant commits its own session (mirrors ghrm/booking).
"""
from uuid import uuid4

import pytest
from flask import current_app

from vbwd.events.bus import EventBus
from vbwd.models.enums import UserRole, UserStatus
from vbwd.models.user import User
from vbwd.plugins.payment_route_helpers import emit_payment_captured

from plugins.dataset import DatasetPlugin, build_dataset_access_service
from plugins.dataset.dataset.models.dataset import Dataset
from plugins.dataset.dataset.models.dataset_access_log import DatasetAccessLog
from plugins.dataset.dataset.models.dataset_membership import (
    DatasetMembershipStatus,
)
from plugins.dataset.dataset.repositories.dataset_membership_repository import (
    DatasetMembershipRepository,
)
from plugins.dataset.dataset.services.dataset_order_service import (
    DatasetOrderService,
)


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


def _make_dataset(db):
    dataset = Dataset()
    dataset.slug = f"ds-{uuid4().hex[:8]}"
    dataset.title = "Air Quality"
    dataset.price = 100.0
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _membership(db, user, dataset):
    return DatasetMembershipRepository(db.session).find_by_user_and_dataset(
        user.id, dataset.id
    )


def test_one_time_order_capture_durably_grants_access(db):
    user = _make_user(db)
    dataset = _make_dataset(db)

    # 1. Create the one-time order exactly as the route does (real service +
    #    the container's PriceFactory) — a PENDING invoice + CUSTOM dataset line.
    order_service = DatasetOrderService(
        db.session, price_factory=current_app.container.price_factory()
    )
    invoice = order_service.create_one_time_order(user.id, dataset)
    db.session.commit()

    # 2. Capture through the REAL path (PaymentCapturedEvent → core handler →
    #    invoice.paid bus publish → the dataset one-time handler). No hand-rolled
    #    on_invoice_paid call.
    result = emit_payment_captured(
        invoice_id=invoice.id,
        payment_reference=f"regression:{invoice.id}",
        amount=invoice.total_amount,
        currency=invoice.currency,
        provider="regression-test",
        transaction_id=str(invoice.id),
    )
    assert result.success, result.error

    # 3. Access was granted and the entitlement read reflects it.
    membership = _membership(db, user, dataset)
    assert membership is not None
    assert membership.status == DatasetMembershipStatus.ACTIVE.value
    assert dataset.id in build_dataset_access_service().active_dataset_ids(user.id)

    # 4. The one-time invoice.paid handler is the path that granted (its audit
    #    trigger is present), i.e. the real publish chain actually reached it.
    triggers = {
        row.triggered_by
        for row in db.session.query(DatasetAccessLog)
        .filter_by(user_id=user.id, dataset_id=dataset.id)
        .all()
    }
    assert "one_time_order" in triggers

    # 5. The grant must be committed, not merely flushed. Rolling the request
    #    session back (the teardown analogue) must NOT remove the membership.
    db.session.rollback()
    survivor = _membership(db, user, dataset)
    assert survivor is not None
    assert survivor.status == DatasetMembershipStatus.ACTIVE.value


def test_one_time_invoice_paid_grant_commits_its_own_session(db):
    """Deterministic guard for the flush→commit fix.

    The full-app capture path (the test above) has downstream ``invoice.paid``
    subscribers that commit the shared session, which would mask a flush-only
    grant. Here the plugin's real ``invoice.paid`` handler is wired onto an
    isolated bus with NO other subscriber, so the ONLY thing that can persist the
    grant past a session rollback is the handler committing its own session. The
    published event uses the exact shape core emits (``invoice_id`` = the invoice
    number). Under a flush-only repo this fails (the grant is rolled back); it
    passes only because the access repos now commit (mirroring ghrm/booking).
    """
    user = _make_user(db)
    dataset = _make_dataset(db)

    order_service = DatasetOrderService(
        db.session, price_factory=current_app.container.price_factory()
    )
    invoice = order_service.create_one_time_order(user.id, dataset)
    db.session.commit()

    # Wire ONLY the dataset plugin's real handlers onto a private bus, then
    # publish the real core ``invoice.paid`` event (keyed by invoice number).
    bus = EventBus()
    plugin = DatasetPlugin()
    plugin.initialize({})
    plugin.register_event_handlers(bus)
    bus.publish(
        "invoice.paid",
        {
            "invoice_id": invoice.invoice_number,
            "amount": str(invoice.total_amount),
            "paid_date": "2026-07-01",
            "invoice_url": f"/invoices/{invoice.id}",
        },
    )

    assert _membership(db, user, dataset).status == (
        DatasetMembershipStatus.ACTIVE.value
    )

    # No downstream subscriber committed for the handler — only its own commit
    # can make the grant survive the request-teardown rollback.
    db.session.rollback()
    survivor = _membership(db, user, dataset)
    assert survivor is not None
    assert survivor.status == DatasetMembershipStatus.ACTIVE.value


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

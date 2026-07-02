"""T6 — the one-time dataset grant path (grant on capture, NO line item).

A one-time dataset purchase does not create a subscription/recurring line item;
the one-time order records the dataset ref in the invoice ``metadata`` (namespace
``dataset``) and access is granted when the payment is captured. The real
post-capture string-bus event is ``invoice.paid`` (booking's grant-on-capture
seam); there is no string ``payment.captured`` bus event.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from plugins.dataset.dataset.handlers.one_time_payment_handler import (
    DatasetOneTimePaymentHandler,
    extract_one_time_dataset_id,
)


def _invoice(user_id, metadata):
    return SimpleNamespace(
        user_id=user_id,
        invoice_number="INV-1",
        payment_metadata=metadata,
        line_items=[],  # deliberately NO dataset line item
    )


def test_extract_reads_dataset_id_from_metadata_namespace():
    dataset_id = str(uuid4())
    invoice = _invoice(uuid4(), {"dataset": {"dataset_id": dataset_id}})
    assert extract_one_time_dataset_id(invoice) == dataset_id


def test_extract_returns_none_without_namespace():
    assert extract_one_time_dataset_id(_invoice(uuid4(), {})) is None
    assert extract_one_time_dataset_id(_invoice(uuid4(), None)) is None


def test_on_invoice_paid_grants_one_time_without_line_item():
    access_service = MagicMock()
    user_id, dataset_id = uuid4(), str(uuid4())
    invoice = _invoice(user_id, {"dataset": {"dataset_id": dataset_id}})

    handler = DatasetOneTimePaymentHandler(
        access_service_factory=lambda: access_service,
        invoice_lookup=lambda number: invoice,
    )
    handler.on_invoice_paid("invoice.paid", {"invoice_id": "INV-1"})

    access_service.grant.assert_called_once()
    assert access_service.grant.call_args.args[0] == user_id
    assert access_service.grant.call_args.args[1] == dataset_id
    # Proven line-item-free: the invoice carried no dataset line item.
    assert invoice.line_items == []


def test_on_invoice_paid_no_grant_when_no_dataset_ref():
    access_service = MagicMock()
    invoice = _invoice(uuid4(), {"stripe": {"charge": "ch_1"}})
    handler = DatasetOneTimePaymentHandler(
        access_service_factory=lambda: access_service,
        invoice_lookup=lambda number: invoice,
    )
    handler.on_invoice_paid("invoice.paid", {"invoice_id": "INV-1"})
    access_service.grant.assert_not_called()


def test_on_invoice_paid_no_grant_for_unknown_invoice():
    access_service = MagicMock()
    handler = DatasetOneTimePaymentHandler(
        access_service_factory=lambda: access_service,
        invoice_lookup=lambda number: None,
    )
    handler.on_invoice_paid("invoice.paid", {"invoice_id": "nope"})
    access_service.grant.assert_not_called()

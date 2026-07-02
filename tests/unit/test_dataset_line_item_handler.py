"""T6 — DatasetLineItemHandler (the "decoration" for recurring dataset lines).

Pure unit tests. A recurring dataset purchase is a CUSTOM line item tagged
``metadata.plugin == "dataset"`` (core's ``LineItemType`` has no DATASET value and
core is read-only, so plugin line types ride CUSTOM + a discriminator, exactly as
shop/booking do). Every grant/revoke delegates to ``DatasetAccessService``.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from vbwd.events.line_item_registry import LineItemContext
from vbwd.models.enums import LineItemType

from plugins.dataset.dataset.handlers.line_item_handler import DatasetLineItemHandler


def _dataset_line_item(dataset_id, extra=None):
    metadata = {"plugin": "dataset", "dataset_id": str(dataset_id)}
    if extra:
        metadata.update(extra)
    return SimpleNamespace(
        item_type=LineItemType.CUSTOM,
        extra_data=metadata,
        description="Air Quality dataset",
    )


def _foreign_line_item():
    return SimpleNamespace(
        item_type=LineItemType.CUSTOM,
        extra_data={"plugin": "shop", "product_id": str(uuid4())},
        description="A shirt",
    )


def _handler():
    access_service = MagicMock()
    handler = DatasetLineItemHandler(lambda: access_service)
    return handler, access_service


def _context(user_id):
    return LineItemContext(invoice=MagicMock(), user_id=user_id, container=MagicMock())


def test_can_handle_only_dataset_custom_lines():
    handler, _ = _handler()
    context = _context(uuid4())
    assert handler.can_handle_line_item(_dataset_line_item(uuid4()), context) is True
    assert handler.can_handle_line_item(_foreign_line_item(), context) is False


def test_activate_grants_access():
    handler, access_service = _handler()
    user_id, dataset_id = uuid4(), uuid4()

    result = handler.activate_line_item(
        _dataset_line_item(dataset_id), _context(user_id)
    )

    assert result.success is True
    access_service.grant.assert_called_once()
    assert access_service.grant.call_args.args[0] == user_id
    assert access_service.grant.call_args.args[1] == str(dataset_id)


def test_reverse_revokes_access():
    handler, access_service = _handler()
    user_id, dataset_id = uuid4(), uuid4()

    handler.reverse_line_item(_dataset_line_item(dataset_id), _context(user_id))

    access_service.revoke.assert_called_once()
    assert access_service.revoke.call_args.args[1] == str(dataset_id)


def test_restore_regrants_access():
    handler, access_service = _handler()
    user_id, dataset_id = uuid4(), uuid4()

    handler.restore_line_item(_dataset_line_item(dataset_id), _context(user_id))

    access_service.restore.assert_called_once()
    assert access_service.restore.call_args.args[1] == str(dataset_id)


def test_resolve_catalog_entity_ref_returns_dataset_pair():
    handler, _ = _handler()
    dataset_id = uuid4()

    ref = handler.resolve_catalog_entity_ref(_dataset_line_item(dataset_id))

    assert ref == ("dataset", str(dataset_id))
    assert handler.resolve_catalog_entity_ref(_foreign_line_item()) is None


def test_is_recurring_true_for_dataset_lines_only():
    handler, _ = _handler()
    assert handler.is_recurring_line_item(_dataset_line_item(uuid4())) is True
    assert handler.is_recurring_line_item(_foreign_line_item()) is False


def test_recurring_billing_spec_present_for_dataset_lines():
    handler, _ = _handler()
    spec = handler.recurring_billing_spec(
        _dataset_line_item(uuid4(), extra={"billing_period": "YEARLY"})
    )
    assert spec is not None
    assert spec.billing_period == "YEARLY"
    assert handler.recurring_billing_spec(_foreign_line_item()) is None

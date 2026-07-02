"""DatasetLineItemHandler — grants dataset access for recurring dataset lines (T6).

The "decoration to a line item": a recurring dataset purchase is a CUSTOM invoice
line item tagged ``metadata.plugin == "dataset"`` (the same seam shop/booking use
— the core ``LineItemType`` enum has no ``DATASET`` value and core is read-only,
so plugin line-item types ride ``CUSTOM`` + a plugin discriminator).

* ``activate_line_item`` grants access on payment capture.
* ``reverse_line_item`` revokes on refund.
* ``restore_line_item`` re-grants on refund reversal.
* ``resolve_catalog_entity_ref`` → ``("dataset", <dataset_id>)`` for the S77
  invoice snapshot.
* ``is_recurring_line_item`` → True: this handler owns the RECURRING dataset path;
  one-time dataset purchases carry no line item and grant through the
  ``invoice.paid`` listener instead (see ``one_time_payment_handler``).

Every grant/revoke delegates to ``DatasetAccessService`` (the single access-state
home — DRY). ghrm is NOT imported.
"""
import logging

from vbwd.events.line_item_registry import (
    ILineItemHandler,
    LineItemContext,
    LineItemResult,
    RecurringBillingSpec,
)
from vbwd.models.enums import LineItemType

logger = logging.getLogger(__name__)

PLUGIN_KEY = "dataset"
TRIGGERED_BY_LINE_ITEM = "line_item"
DEFAULT_BILLING_PERIOD = "MONTHLY"


class DatasetLineItemHandler(ILineItemHandler):
    """Handles CUSTOM line items where ``metadata.plugin == "dataset"``."""

    def __init__(self, access_service_factory) -> None:
        # A zero-arg callable that builds a ``DatasetAccessService`` bound to a
        # fresh ``db.session`` (composition root). Matches ghrm's per-call
        # service construction so handlers never hold a stale session.
        self._make_access_service = access_service_factory

    def can_handle_line_item(self, line_item, context: LineItemContext) -> bool:
        return self._is_dataset_line(line_item)

    @staticmethod
    def _is_dataset_line(line_item) -> bool:
        """Context-free discriminator (used by the poll-style resolve methods)."""
        return (
            line_item.item_type == LineItemType.CUSTOM
            and (line_item.extra_data or {}).get("plugin") == PLUGIN_KEY
        )

    def _dataset_id(self, line_item):
        return (line_item.extra_data or {}).get("dataset_id")

    def activate_line_item(self, line_item, context: LineItemContext) -> LineItemResult:
        """On payment capture: grant ACTIVE dataset access."""
        dataset_id = self._dataset_id(line_item)
        if not dataset_id:
            return LineItemResult(success=True, data={})
        self._make_access_service().grant(
            context.user_id, dataset_id, triggered_by=TRIGGERED_BY_LINE_ITEM
        )
        return LineItemResult(success=True, data={"dataset_id": str(dataset_id)})

    def reverse_line_item(self, line_item, context: LineItemContext) -> LineItemResult:
        """On refund: revoke dataset access."""
        dataset_id = self._dataset_id(line_item)
        if not dataset_id:
            return LineItemResult(success=True, data={})
        self._make_access_service().revoke(
            context.user_id, dataset_id, triggered_by=TRIGGERED_BY_LINE_ITEM
        )
        return LineItemResult(success=True, data={"dataset_id": str(dataset_id)})

    def restore_line_item(self, line_item, context: LineItemContext) -> LineItemResult:
        """On refund reversal: re-grant dataset access."""
        dataset_id = self._dataset_id(line_item)
        if not dataset_id:
            return LineItemResult(success=True, data={})
        self._make_access_service().restore(
            context.user_id, dataset_id, triggered_by=TRIGGERED_BY_LINE_ITEM
        )
        return LineItemResult(success=True, data={"dataset_id": str(dataset_id)})

    def resolve_catalog_entity_ref(self, line_item):
        """Source entity ref for the S77 invoice snapshot → ``("dataset", id)``.

        ``None`` for any line item this handler does not own.
        """
        if not self._is_dataset_line(line_item):
            return None
        dataset_id = self._dataset_id(line_item)
        return ("dataset", str(dataset_id)) if dataset_id else None

    def is_recurring_line_item(self, line_item) -> bool:
        """A dataset line item this handler owns is the RECURRING path.

        False for any line item this handler does not own.
        """
        return self._is_dataset_line(line_item)

    def recurring_billing_spec(self, line_item):
        """Billing spec so a provider can set up the recurring dataset charge."""
        if not self._is_dataset_line(line_item):
            return None
        extra = line_item.extra_data or {}
        return RecurringBillingSpec(
            name=extra.get("dataset_title") or line_item.description,
            billing_period=extra.get("billing_period") or DEFAULT_BILLING_PERIOD,
        )

"""DatasetOneTimePaymentHandler — grants dataset access for one-time orders (T6).

The one-time (non-recurring) counterpart to ``DatasetLineItemHandler``. A one-time
dataset purchase does NOT create a subscription/recurring line item; instead the
one-time order records the dataset ref in the invoice's ``metadata`` (namespace
``dataset``) and access is granted when the payment is captured.

The real post-capture string-bus seam is ``invoice.paid`` (published by core's
``PaymentCapturedHandler`` after it marks the invoice PAID and merges the
plugin-contributed ``metadata``). There is no string ``payment.captured`` bus
event — that name exists only on the domain-event dispatcher. This is exactly the
seam the booking plugin (the platform's canonical one-time purchase) uses to
"grant on capture".

The grant delegates to ``DatasetAccessService`` (the single access-state home —
DRY). ghrm is NOT imported.
"""
import logging

logger = logging.getLogger(__name__)

METADATA_NAMESPACE = "dataset"
TRIGGERED_BY_ONE_TIME = "one_time_order"


def extract_one_time_dataset_id(invoice):
    """Return the dataset id a captured one-time order grants, or ``None``.

    Reads the invoice's ``metadata`` (the core-agnostic per-plugin namespace):
    ``{"dataset": {"dataset_id": "<uuid>"}}``. Pure + unit-testable — no line
    item is involved (the one-time path is deliberately line-item-free).
    """
    dataset_namespace = (getattr(invoice, "payment_metadata", None) or {}).get(
        METADATA_NAMESPACE
    )
    if not isinstance(dataset_namespace, dict):
        return None
    return dataset_namespace.get("dataset_id")


class DatasetOneTimePaymentHandler:
    """Grants dataset access on ``invoice.paid`` for one-time dataset orders."""

    def __init__(self, access_service_factory, invoice_lookup) -> None:
        # ``access_service_factory``: zero-arg callable → ``DatasetAccessService``
        # bound to a fresh ``db.session``. ``invoice_lookup``: callable
        # (invoice_number) → invoice | None.
        self._make_access_service = access_service_factory
        self._invoice_lookup = invoice_lookup

    def on_invoice_paid(self, event_name: str, data: dict) -> None:
        """EventBus callback — signature ``(event_name, data)``."""
        invoice_number = data.get("invoice_id")
        if not invoice_number:
            return
        invoice = self._invoice_lookup(invoice_number)
        if invoice is None:
            return
        dataset_id = extract_one_time_dataset_id(invoice)
        if not dataset_id:
            return
        self._make_access_service().grant(
            invoice.user_id, dataset_id, triggered_by=TRIGGERED_BY_ONE_TIME
        )
        logger.info(
            "[dataset] One-time order granted access to dataset %s for user %s",
            dataset_id,
            invoice.user_id,
        )

"""DatasetOrderService — one-time dataset purchase invoice creation (S110 T6).

The one-time (non-recurring) differentiator. A one-time dataset purchase does
NOT create a subscription/recurring line item; instead it creates an invoice
with a single ``LineItemType.CUSTOM`` line tagged ``extra_data.plugin='dataset'``
(so the fe-user invoice detail's CUSTOM fall-through resolves it to the dataset
access page) and stamps the invoice's ``payment_metadata`` under the ``dataset``
namespace — the exact shape ``DatasetOneTimePaymentHandler.on_invoice_paid``
reads to grant access when ``invoice.paid`` fires.

Mirrors the platform's canonical one-time purchase (``BookingInvoiceService``):
the charged unit price is the computed ``Price.brutto`` from the core
``PriceFactory`` (D8), and the per-line netto/tax split is recorded via the
shared ``line_tax_fields`` helper (DRY — no tax math here). Access is granted by
the ``invoice.paid`` one-time handler, not by a line-item activation handler, so
the CUSTOM line needs no backend activation handler.
"""
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from vbwd.models.enums import InvoiceStatus, LineItemType
from vbwd.models.invoice import UserInvoice
from vbwd.models.invoice_line_item import InvoiceLineItem
from vbwd.pricing.line_tax_fields import line_tax_fields

from plugins.dataset.dataset.constants import VENDOR_ID_KEY
from plugins.dataset.dataset.services.plugin_config import marketplace_enabled

# The invoice's free-form ``payment_metadata`` namespace the one-time handler
# reads. Kept in lockstep with ``one_time_payment_handler.METADATA_NAMESPACE``.
METADATA_NAMESPACE = "dataset"

_CENTS = Decimal("0.01")


class DatasetOrderService:
    """Create the invoice + CUSTOM line + metadata for a one-time dataset buy."""

    def __init__(self, session, price_factory, invoice_prefix: str = "DS") -> None:
        if price_factory is None:
            raise ValueError(
                "DatasetOrderService requires a PriceFactory to derive the "
                "charged brutto price and per-line tax breakdown."
            )
        self._session = session
        self._invoice_prefix = invoice_prefix
        self._price_factory = price_factory

    def create_one_time_order(self, user_id, dataset) -> UserInvoice:
        """Create a PENDING invoice with the dataset CUSTOM line + metadata.

        The invoice is left PENDING; the caller captures it (zero-price fast
        path or the buyer's selected payment method), and the ``invoice.paid``
        one-time handler grants access from the ``payment_metadata`` stamped
        here.
        """
        computed_price = self._price_factory.get_price_from_object(dataset)
        unit_price = Decimal(str(computed_price.brutto)).quantize(
            _CENTS, rounding=ROUND_HALF_UP
        )
        total_amount = unit_price
        breakdown = computed_price.to_dict()
        tax_fields = line_tax_fields(computed_price, quantity=1)

        invoice = UserInvoice()
        invoice.user_id = user_id
        invoice.invoice_number = (
            f"{self._invoice_prefix}-{uuid.uuid4().hex[:8].upper()}"
        )
        invoice.amount = total_amount
        invoice.subtotal = total_amount
        invoice.total_amount = total_amount
        invoice.status = InvoiceStatus.PENDING
        invoice.invoiced_at = datetime.utcnow()
        # Stamp the dataset namespace the one-time handler consumes on
        # ``invoice.paid``. Reassign the dict so SQLAlchemy tracks the JSON.
        invoice.payment_metadata = {
            METADATA_NAMESPACE: {
                "dataset_id": str(dataset.id),
                "dataset_slug": dataset.slug,
            }
        }
        self._session.add(invoice)
        self._session.flush()

        line_item = InvoiceLineItem()
        line_item.invoice_id = invoice.id
        line_item.item_type = LineItemType.CUSTOM
        line_item.item_id = dataset.id
        line_item.description = dataset.title
        line_item.quantity = 1
        line_item.unit_price = unit_price
        line_item.total_price = total_amount
        line_item.extra_data = {
            "plugin": METADATA_NAMESPACE,
            "dataset_id": str(dataset.id),
            "dataset_slug": dataset.slug,
            "dataset_title": dataset.title,
            "price_breakdown": breakdown,
        }
        # Vendor-mode (marketplace): stamp the selling vendor's user id onto the
        # buyer invoice line so the central marketplace plugin credits the vendor
        # on ``invoice.paid``. Merged (never clobbers other keys), only for
        # vendor-owned datasets, only when vendor-mode is enabled — dataset stamps
        # a LOCAL literal and never imports marketplace.
        if marketplace_enabled() and dataset.vendor_id is not None:
            line_item.extra_data[VENDOR_ID_KEY] = str(dataset.vendor_id)
        line_item.net_amount = tax_fields["net_amount"]
        line_item.tax_amount = tax_fields["tax_amount"]
        line_item.tax_breakdown = tax_fields["tax_breakdown"]
        invoice.subtotal = tax_fields["net_amount"]
        invoice.tax_amount = tax_fields["tax_amount"]
        invoice.total_amount = total_amount
        self._session.add(line_item)
        self._session.flush()

        return invoice

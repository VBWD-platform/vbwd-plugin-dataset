"""Dataset model — a sellable, versioned data product (S110 T2).

A ``Dataset`` is a catalogue row that conforms to the core ``Priceable``
protocol (``raw_price`` + ``taxes``), so ``PriceFactory.get_price_from_object``
turns it into a computed ``Price`` with no core change and no registration.

The row is the head of a versioned archive: many timestamped
``DatasetSnapshot`` rows point back at it, and ``last_snapshot_id`` is the
pointer to the newest one (the ``last`` field). ``last_snapshot_id`` is a soft
pointer (a plain UUID column, no DB-level foreign key) to avoid a circular
foreign key with ``dataset_snapshot.dataset_id``.
"""
from typing import Optional

from sqlalchemy.dialects.postgresql import UUID

from vbwd.extensions import db
from vbwd.models.base import BaseModel

# Imported so the ``dataset_plans`` relationship's mapper is registered whatever
# the import order (dataset_plan does not import dataset, so no cycle).
from plugins.dataset.dataset.models.dataset_plan import DatasetPlan


# A per-dataset netto/brutto price-display override. ``None`` inherits the
# global ``prices_display_mode`` core setting; ``"netto"``/``"brutto"`` override
# it. Mirrors the booking resource convention (S72.4).
PRICE_DISPLAY_MODE_OVERRIDES = ("netto", "brutto")


def validate_price_display_mode(value: Optional[str]) -> Optional[str]:
    """Return ``value`` if it is a valid override, else raise ``ValueError``.

    ``None`` (inherit the global setting) and the two enum values are accepted;
    any other value is rejected so the admin route can map it to a 400.
    """
    if value is None or value in PRICE_DISPLAY_MODE_OVERRIDES:
        return value
    raise ValueError(
        "price_display_mode must be one of "
        f"{(None,) + PRICE_DISPLAY_MODE_OVERRIDES}, got {value!r}"
    )


# Many-to-many join to the CORE tax catalog (``vbwd_tax``). The ``tax_id`` FK
# uses ``ON DELETE RESTRICT`` so deleting a tax assigned to a dataset is rejected
# by the database rather than silently dropping the link; the ``dataset_id`` FK
# uses ``ON DELETE CASCADE`` so deleting a dataset tidies its own links. Mirrors
# the booking resource tax link.
dataset_tax = db.Table(
    "dataset_tax",
    db.Column(
        "dataset_id",
        UUID(as_uuid=True),
        db.ForeignKey("dataset.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "tax_id",
        UUID(as_uuid=True),
        db.ForeignKey("vbwd_tax.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class Dataset(BaseModel):
    """A sellable dataset — the head of a versioned snapshot archive."""

    __tablename__ = "dataset"

    slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    source_attribution = db.Column(db.Text, nullable=True)

    # The single stored price double (full precision, never rounded in code);
    # the currency is the global ``default_currency`` (S84/S85). ``raw_price``
    # exposes it under the name the ``Priceable`` protocol requires.
    price = db.Column(db.Float, nullable=False, default=0.0)

    # Per-dataset netto/brutto display override; ``NULL`` inherits the global
    # setting.
    price_display_mode = db.Column(db.String(8), nullable=True)

    # The ``last`` pointer — the newest ``DatasetSnapshot``. A soft UUID pointer
    # (no DB foreign key) to avoid a circular FK with ``dataset_snapshot``.
    last_snapshot_id = db.Column(UUID(as_uuid=True), nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # Vendor-mode (marketplace): the owning vendor's ``vbwd_user`` id. ``NULL``
    # is a platform-owned dataset. Indexed for the vendor's "my datasets" filter;
    # ``ON DELETE SET NULL`` so removing a user reverts their datasets to the
    # platform rather than deleting the catalogue rows. Mirrors shop's product.
    vendor_id = db.Column(
        db.UUID,
        db.ForeignKey("vbwd_user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Assigned core taxes (M2M). When present these drive the ``Priceable``
    # tax breakdown.
    taxes = db.relationship(
        "Tax",
        secondary=dataset_tax,
        lazy="selectin",
    )

    # Read-only view of the dataset ↔ tariff-plan grant links (``DatasetPlan``).
    # ``viewonly`` keeps the relationship out of flush/cascade — link writes go
    # through ``DatasetService.set_tariff_plan_link`` and deletes rely on the
    # DB-level ``ON DELETE CASCADE`` on ``dataset_plan.dataset_id``.
    dataset_plans = db.relationship(
        DatasetPlan,
        lazy="selectin",
        viewonly=True,
    )

    @property
    def raw_price(self) -> float:
        """The stored price as a float (the ``Priceable`` protocol member)."""
        return float(self.price) if self.price is not None else 0.0

    def _linked_tariff_plan_id(self):
        """The tariff plan that grants this dataset (first link), or ``None``.

        MVP surfaces a single plan link (the admin editor's plan select). The FK
        is not unique so several links may exist; the first is returned.
        """
        plans = getattr(self, "dataset_plans", None) or []
        return str(plans[0].tariff_plan_id) if plans else None

    def _serialize_taxes(self) -> list:
        """Serialize assigned core taxes to ``{id, code, name, rate}``."""
        taxes = getattr(self, "taxes", None) or []
        return [
            {
                "id": str(tax.id),
                "code": tax.code,
                "name": tax.name,
                "rate": str(tax.rate),
            }
            for tax in taxes
        ]

    def to_dict(self) -> dict:
        taxes = self._serialize_taxes()
        return {
            "id": str(self.id),
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "source_attribution": self.source_attribution,
            "price": self.raw_price,
            "price_display_mode": self.price_display_mode,
            "tariff_plan_id": self._linked_tariff_plan_id(),
            "last_snapshot_id": (
                str(self.last_snapshot_id) if self.last_snapshot_id else None
            ),
            "is_active": self.is_active,
            "vendor_id": str(self.vendor_id) if self.vendor_id else None,
            "tax_ids": [tax["id"] for tax in taxes],
            "taxes": taxes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Dataset(slug='{self.slug}', title='{self.title}')>"

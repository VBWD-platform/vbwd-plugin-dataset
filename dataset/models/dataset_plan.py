"""DatasetPlan model — links a dataset to the tariff plan that grants it (T7).

Copied in shape from ghrm's ``GhrmSoftwarePackage.tariff_plan_id`` +
``find_by_tariff_plan_id``: a plan-grants-access link so buying (subscribing to)
the plan entitles the buyer to the dataset. This is the concrete mechanism the
``IDatasetEntitlements`` port projects over — active plans → linked datasets.

Unlike ghrm (one package per plan, ``unique`` FK) a plan may unlock several
datasets and a dataset may be sold under several plans, so the FK is NOT unique;
the ``(dataset_id, tariff_plan_id)`` pair is what must be unique.

ghrm is NOT imported — only its pattern is reused.
"""
from sqlalchemy.dialects.postgresql import UUID

from vbwd.extensions import db
from vbwd.models.base import BaseModel


class DatasetPlan(BaseModel):
    """A dataset ↔ ``subscription_tarif_plan`` grant link."""

    __tablename__ = "dataset_plan"

    dataset_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tariff_plan_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("subscription_tarif_plan.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "dataset_id", "tariff_plan_id", name="uq_dataset_plan_dataset_plan"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "dataset_id": str(self.dataset_id),
            "tariff_plan_id": str(self.tariff_plan_id),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<DatasetPlan(dataset_id='{self.dataset_id}', "
            f"tariff_plan_id='{self.tariff_plan_id}')>"
        )

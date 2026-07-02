"""DatasetAccessLog model — audit trail for dataset access changes (T7).

Copied in shape from ghrm's ``GhrmAccessLog``: an append-only record of every
grant / grace-start / revoke, tagged with what triggered it (a subscription
event, a line item, a one-time order, or the scheduler).

ghrm is NOT imported — only its pattern is reused.
"""
from sqlalchemy.dialects.postgresql import UUID

from vbwd.extensions import db
from vbwd.models.base import BaseModel


class DatasetAccessAction:
    """The audited access transitions (single source of truth)."""

    GRANT = "grant"
    GRACE_STARTED = "grace_started"
    REVOKE = "revoke"
    RESTORE = "restore"


class DatasetAccessLog(BaseModel):
    """One audited dataset-access transition."""

    __tablename__ = "dataset_access_log"

    user_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("vbwd_user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    dataset_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("dataset.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action = db.Column(db.String(32), nullable=False)
    # What caused the transition: subscription_event | line_item |
    # one_time_order | scheduler | manual.
    triggered_by = db.Column(db.String(64), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "dataset_id": str(self.dataset_id) if self.dataset_id else None,
            "action": self.action,
            "triggered_by": self.triggered_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

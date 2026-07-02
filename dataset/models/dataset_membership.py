"""DatasetMembership model — per-(user, dataset) access state (T7).

Copied in shape from ghrm's ``GhrmRepoMembership``: one row per (user, dataset)
tracking the entitlement lifecycle ``INVITED → ACTIVE → GRACE → REVOKED``. Unlike
ghrm there is no external-system invitation handshake, so a grant moves straight
to ACTIVE; ``INVITED`` stays in the vocabulary for parity.

ghrm is NOT imported — only its pattern is reused.
"""
import enum

from sqlalchemy.dialects.postgresql import UUID

from vbwd.extensions import db
from vbwd.models.base import BaseModel

STATUS_COLUMN_LENGTH = 16


class DatasetMembershipStatus(str, enum.Enum):
    """Lifecycle states for a user's access to a dataset."""

    INVITED = "invited"
    ACTIVE = "active"
    GRACE = "grace"
    REVOKED = "revoked"


class DatasetMembership(BaseModel):
    """A user's access record for one dataset."""

    __tablename__ = "dataset_membership"

    user_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("vbwd_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = db.Column(
        db.String(STATUS_COLUMN_LENGTH),
        nullable=False,
        default=DatasetMembershipStatus.INVITED.value,
        index=True,
    )
    granted_at = db.Column(db.DateTime, nullable=True)
    grace_expires_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "dataset_id", name="uq_dataset_membership_user_dataset"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "dataset_id": str(self.dataset_id),
            "status": self.status,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
            "grace_expires_at": (
                self.grace_expires_at.isoformat() if self.grace_expires_at else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<DatasetMembership(user_id='{self.user_id}', "
            f"dataset_id='{self.dataset_id}', status='{self.status}')>"
        )

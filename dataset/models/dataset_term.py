"""DatasetTerm model — dataset↔term junction (S110 T4).

Categories reuse the shared cms term engine (``cms_term`` with
``term_type='dataset_category'``). This NET-NEW junction links a dataset to one
or more of those terms. Mirrors ``CmsPostTerm``: both foreign keys cascade on
delete, so removing a dataset (or a term) drops the junction rows but leaves the
other side intact.
"""
from sqlalchemy import UniqueConstraint

from vbwd.extensions import db
from vbwd.models.base import BaseModel


class DatasetTerm(BaseModel):
    """Many-to-many link between a dataset and a taxonomy term."""

    __tablename__ = "dataset_term"
    __table_args__ = (
        UniqueConstraint("dataset_id", "term_id", name="uq_dataset_term"),
    )

    dataset_id = db.Column(
        db.UUID,
        db.ForeignKey("dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    term_id = db.Column(
        db.UUID,
        db.ForeignKey("cms_term.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "dataset_id": str(self.dataset_id),
            "term_id": str(self.term_id),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<DatasetTerm(dataset={self.dataset_id}, term={self.term_id})>"

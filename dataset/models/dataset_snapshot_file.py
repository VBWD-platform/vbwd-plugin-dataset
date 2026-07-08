"""DatasetSnapshotFile model — an extra file attached to one issue (S124).

An *issue* (a :class:`DatasetSnapshot`) keeps its primary tabular data file on
the snapshot itself (``location``/``ext``, unchanged). S124 lets an issue carry
additional companion files — a PDF report, one or more charts, other artefacts —
each recorded as a ``dataset_snapshot_file`` row under the same issue.

The child row stores where its bytes live (backend-relative ``location``, never a
public URL) plus a fixed **role** so the UI can badge each file. Roles are a
closed constant set (single source of truth here) — no free-form labels.
"""
from sqlalchemy.dialects.postgresql import UUID

from vbwd.extensions import db
from vbwd.models.base import BaseModel


# The fixed role vocabulary for a companion file. Kept here as the single source
# of truth so the service validation, the routes and the FE agree on the exact
# strings. NOT free-form — an unknown role is rejected at attach time.
FILE_ROLE_DATA = "data"
FILE_ROLE_DOCUMENT = "document"
FILE_ROLE_CHART = "chart"
FILE_ROLE_OTHER = "other"
ALLOWED_FILE_ROLES = (
    FILE_ROLE_DATA,
    FILE_ROLE_DOCUMENT,
    FILE_ROLE_CHART,
    FILE_ROLE_OTHER,
)


class DatasetSnapshotFile(BaseModel):
    """One companion file attached to a dataset issue (snapshot)."""

    __tablename__ = "dataset_snapshot_file"

    snapshot_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("dataset_snapshot.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = db.Column(db.String(16), nullable=False, default=FILE_ROLE_OTHER)
    filename = db.Column(db.String(255), nullable=False)
    storage_backend = db.Column(db.String(16), nullable=False)
    # Backend-relative location of the file's bytes (never a public URL).
    location = db.Column(db.String(1024), nullable=False)
    ext = db.Column(db.String(16), nullable=False)
    content_type = db.Column(db.String(128), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    checksum = db.Column(db.String(128), nullable=True)

    def to_dict(self) -> dict:
        """Public projection — deliberately WITHOUT the raw ``location``.

        The storage path is an internal detail; callers stream the bytes through
        the entitlement-gated download route, never by the raw location.
        """
        return {
            "id": str(self.id),
            "snapshot_id": str(self.snapshot_id),
            "role": self.role,
            "filename": self.filename,
            "ext": self.ext,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<DatasetSnapshotFile(snapshot_id='{self.snapshot_id}', "
            f"role='{self.role}', filename='{self.filename}')>"
        )

"""DatasetSnapshot model — one versioned archive file of a dataset (S110 T2).

A dataset is a living series; each refresh is a snapshot. The catalogue row
(``Dataset``) keeps a ``last_snapshot_id`` pointer to the newest one. A snapshot
records how and where its bytes are stored so the read API / dashboard can serve
them through the storage backend, entitlement-gated (the ``location`` is
backend-relative and never a public URL).
"""
from sqlalchemy.dialects.postgresql import UUID

from vbwd.extensions import db
from vbwd.models.base import BaseModel


# How a snapshot's bytes entered the archive. Single source of truth so routes,
# webhooks and the exporter agree.
INGESTED_VIA_UPLOAD = "upload"
INGESTED_VIA_WEBHOOK = "webhook"
INGESTED_VIA_SYNC = "sync"
ALLOWED_INGESTED_VIA = (INGESTED_VIA_UPLOAD, INGESTED_VIA_WEBHOOK, INGESTED_VIA_SYNC)

# Which storage backend holds the bytes. Local is the MVP default; ``aws`` is
# added behind the same port in S110 T3.
STORAGE_BACKEND_LOCAL = "local"
STORAGE_BACKEND_AWS = "aws"


class DatasetSnapshot(BaseModel):
    """One timestamped version of a dataset's data file."""

    __tablename__ = "dataset_snapshot"

    dataset_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The ``YYYY-MM-DD-HH-mm`` token — also the on-disk filename stem, kept as a
    # string so the location derivation stays DRY with the archive path.
    taken_at = db.Column(db.String(32), nullable=False)
    storage_backend = db.Column(
        db.String(16), nullable=False, default=STORAGE_BACKEND_LOCAL
    )
    # Backend-relative location (e.g. the archive path under the ``var``
    # namespace, or ``bucket/prefix/...`` for S3). NEVER a public URL.
    location = db.Column(db.String(1024), nullable=False)
    ext = db.Column(db.String(16), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    checksum = db.Column(db.String(128), nullable=True)
    ingested_via = db.Column(db.String(16), nullable=False, default=INGESTED_VIA_UPLOAD)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "dataset_id": str(self.dataset_id),
            "taken_at": self.taken_at,
            "storage_backend": self.storage_backend,
            "location": self.location,
            "ext": self.ext,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "ingested_via": self.ingested_via,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<DatasetSnapshot(dataset_id='{self.dataset_id}', "
            f"taken_at='{self.taken_at}')>"
        )

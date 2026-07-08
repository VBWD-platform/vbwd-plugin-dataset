"""Create the ``dataset_snapshot_file`` table (S124).

ADDITIVE ONLY. An issue (``dataset_snapshot``) may now carry N companion files
(a PDF report, charts, other artefacts) as child rows; the primary tabular data
file stays on the snapshot itself (unchanged, zero backfill).

Anchors on the dataset plugin's own current head (``20260704_1000_dataset_vendor_id``)
so the chain resolves with the dataset plugin alone (core stays
standalone-resolvable). The FK ``ondelete=CASCADE`` means deleting an issue drops
its companion rows with it.

Revision ID: 20260707_1000_dataset_snapshot_file
Revises: 20260704_1000_dataset_vendor_id
Create Date: 2026-07-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260707_1000_dataset_snapshot_file"
down_revision = "20260704_1000_dataset_vendor_id"
branch_labels = None
depends_on = None

_TABLE = "dataset_snapshot_file"
_SNAPSHOT_TABLE = "dataset_snapshot"
_INDEX = "ix_dataset_snapshot_file_snapshot_id"
_FK = "fk_dataset_snapshot_file_snapshot_id"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="other"),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_backend", sa.String(length=16), nullable=False),
        sa.Column("location", sa.String(length=1024), nullable=False),
        sa.Column("ext", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            [f"{_SNAPSHOT_TABLE}.id"],
            name=_FK,
            ondelete="CASCADE",
        ),
    )
    op.create_index(_INDEX, _TABLE, ["snapshot_id"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)

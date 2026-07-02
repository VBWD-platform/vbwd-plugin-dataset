"""Create dataset, dataset_snapshot and dataset_tax tables (S110 T2).

ADDITIVE ONLY. Creates the datasets vertical's own tables. Anchors on
``vbwd_001`` (the monolithic core baseline that creates ``vbwd_tax``, the only
external FK target) so it resolves standalone whenever core is present — no
dependency on the subscription/cms migration trees (the dataset<->term junction,
which FKs ``cms_term``, arrives in T4 as its own migration).

``dataset.last_snapshot_id`` is a soft UUID pointer (no DB foreign key) to avoid
a circular FK with ``dataset_snapshot.dataset_id``.

Revision ID: 20260701_1000_dataset_tables
Revises: vbwd_001
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260701_1000_dataset_tables"
down_revision = "vbwd_001"
branch_labels = None
depends_on = None

DATASET_TABLE = "dataset"
SNAPSHOT_TABLE = "dataset_snapshot"
TAX_LINK_TABLE = "dataset_tax"


def upgrade():
    op.create_table(
        DATASET_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_attribution", sa.Text(), nullable=True),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price_display_mode", sa.String(length=8), nullable=True),
        sa.Column("last_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("slug", name="uq_dataset_slug"),
    )
    op.create_index("ix_dataset_slug", DATASET_TABLE, ["slug"])

    op.create_table(
        SNAPSHOT_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taken_at", sa.String(length=32), nullable=False),
        sa.Column(
            "storage_backend",
            sa.String(length=16),
            nullable=False,
            server_default="local",
        ),
        sa.Column("location", sa.String(length=1024), nullable=False),
        sa.Column("ext", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column(
            "ingested_via",
            sa.String(length=16),
            nullable=False,
            server_default="upload",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"], [f"{DATASET_TABLE}.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_dataset_snapshot_dataset_id", SNAPSHOT_TABLE, ["dataset_id"])

    op.create_table(
        TAX_LINK_TABLE,
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tax_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.ForeignKeyConstraint(
            ["dataset_id"], [f"{DATASET_TABLE}.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tax_id"], ["vbwd_tax.id"], ondelete="RESTRICT"),
    )


def downgrade():
    op.drop_table(TAX_LINK_TABLE)
    op.drop_index("ix_dataset_snapshot_dataset_id", table_name=SNAPSHOT_TABLE)
    op.drop_table(SNAPSHOT_TABLE)
    op.drop_index("ix_dataset_slug", table_name=DATASET_TABLE)
    op.drop_table(DATASET_TABLE)

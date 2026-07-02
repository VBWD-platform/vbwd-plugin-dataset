"""Create the dataset_term junction (dataset↔cms_term) (S110 T4).

ADDITIVE ONLY. Links a dataset to shared ``cms_term`` rows of
``term_type='dataset_category'``. Both foreign keys cascade on delete.

Anchors (``down_revision``) on the plugin's own prior head
(``20260701_1000_dataset_tables``) to keep the dataset chain linear. The FK to
``cms_term`` is expressed as an alembic ``depends_on`` on the cms revision that
creates that table (``20260603_1000_cms_unified``) so ``upgrade heads`` always
runs the cms migration first — cms is a declared hard dependency of this plugin,
so that revision is always present ([[project_migration_graph_fragmentation]]).

Revision ID: 20260701_1100_dataset_term
Revises: 20260701_1000_dataset_tables
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260701_1100_dataset_term"
down_revision = "20260701_1000_dataset_tables"
branch_labels = None
depends_on = "20260603_1000_cms_unified"

JUNCTION_TABLE = "dataset_term"


def upgrade():
    op.create_table(
        JUNCTION_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["term_id"], ["cms_term.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dataset_id", "term_id", name="uq_dataset_term"),
    )
    op.create_index("ix_dataset_term_dataset_id", JUNCTION_TABLE, ["dataset_id"])
    op.create_index("ix_dataset_term_term_id", JUNCTION_TABLE, ["term_id"])


def downgrade():
    op.drop_index("ix_dataset_term_term_id", table_name=JUNCTION_TABLE)
    op.drop_index("ix_dataset_term_dataset_id", table_name=JUNCTION_TABLE)
    op.drop_table(JUNCTION_TABLE)

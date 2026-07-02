"""Create dataset_plan, dataset_membership, dataset_access_log tables (S110 T7).

ADDITIVE ONLY. The entitlement + access lifecycle tables (copied in shape from
ghrm's software-package / repo-membership / access-log trio, no ghrm import).

Anchors (``down_revision``) on the plugin's own prior head
(``20260701_1100_dataset_term``) to keep the dataset chain linear. ``dataset_plan``
FKs ``subscription_tarif_plan``, so an alembic ``depends_on`` on the subscription
revision that gives that table its final name (``20260531_subscription_prefix``)
guarantees ``upgrade heads`` runs subscription first — subscription is a declared
hard dependency of this plugin, so that revision is always present
([[project_migration_graph_fragmentation]]). The other FK targets (``dataset``,
``vbwd_user``) are the plugin's own table and the core baseline.

Revision ID: 20260701_1200_dataset_access
Revises: 20260701_1100_dataset_term
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260701_1200_dataset_access"
down_revision = "20260701_1100_dataset_term"
branch_labels = None
depends_on = "20260531_subscription_prefix"

PLAN_TABLE = "dataset_plan"
MEMBERSHIP_TABLE = "dataset_membership"
LOG_TABLE = "dataset_access_log"


def upgrade():
    op.create_table(
        PLAN_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tariff_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tariff_plan_id"], ["subscription_tarif_plan.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "dataset_id", "tariff_plan_id", name="uq_dataset_plan_dataset_plan"
        ),
    )
    op.create_index("ix_dataset_plan_dataset_id", PLAN_TABLE, ["dataset_id"])
    op.create_index("ix_dataset_plan_tariff_plan_id", PLAN_TABLE, ["tariff_plan_id"])

    op.create_table(
        MEMBERSHIP_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=True),
        sa.Column("grace_expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["vbwd_user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id", "dataset_id", name="uq_dataset_membership_user_dataset"
        ),
    )
    op.create_index("ix_dataset_membership_user_id", MEMBERSHIP_TABLE, ["user_id"])
    op.create_index(
        "ix_dataset_membership_dataset_id", MEMBERSHIP_TABLE, ["dataset_id"]
    )
    op.create_index("ix_dataset_membership_status", MEMBERSHIP_TABLE, ["status"])

    op.create_table(
        LOG_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("triggered_by", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["vbwd_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_dataset_access_log_user_id", LOG_TABLE, ["user_id"])
    op.create_index("ix_dataset_access_log_dataset_id", LOG_TABLE, ["dataset_id"])


def downgrade():
    op.drop_index("ix_dataset_access_log_dataset_id", table_name=LOG_TABLE)
    op.drop_index("ix_dataset_access_log_user_id", table_name=LOG_TABLE)
    op.drop_table(LOG_TABLE)

    op.drop_index("ix_dataset_membership_status", table_name=MEMBERSHIP_TABLE)
    op.drop_index("ix_dataset_membership_dataset_id", table_name=MEMBERSHIP_TABLE)
    op.drop_index("ix_dataset_membership_user_id", table_name=MEMBERSHIP_TABLE)
    op.drop_table(MEMBERSHIP_TABLE)

    op.drop_index("ix_dataset_plan_tariff_plan_id", table_name=PLAN_TABLE)
    op.drop_index("ix_dataset_plan_dataset_id", table_name=PLAN_TABLE)
    op.drop_table(PLAN_TABLE)

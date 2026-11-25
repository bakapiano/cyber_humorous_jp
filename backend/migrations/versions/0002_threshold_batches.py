"""Persist quantity triggers and allow multiple immutable batches on one day."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table(
        "production_batches", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}
    ) as batch:
        batch.drop_constraint("uq_production_batches_local_date", type_="unique")
        batch.add_column(sa.Column("run_key", sa.String(), nullable=True))
        batch.add_column(sa.Column("execution_json", sa.Text(), nullable=True))
        batch.create_unique_constraint("uq_production_batches_run_key", ["run_key"])
        batch.create_index("ix_production_batches_local_date", ["local_date"])
    op.create_table(
        "workflow_settings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is intentionally unsupported")

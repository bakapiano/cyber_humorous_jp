"""Keep old snapshots while explicitly releasing never-executed reservations."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "production_batches",
        sa.Column("reservations_released", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is intentionally unsupported")

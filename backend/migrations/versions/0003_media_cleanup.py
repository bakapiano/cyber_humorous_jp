"""Keep artifact identities after verified batch media cleanup."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("cleaned_at", sa.String(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is intentionally unsupported")

"""reports.incidents.investigation: investigation graph'ın sonucu (T3.2)

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

S = "reports"


def upgrade() -> None:
    op.add_column("incidents", sa.Column("investigation", pg.JSONB), schema=S)
    op.add_column("incidents", sa.Column("investigated_at", sa.DateTime(timezone=True)), schema=S)
    op.add_column("incidents", sa.Column("investigation_mode", sa.String(10)), schema=S)


def downgrade() -> None:
    for col in ("investigation_mode", "investigated_at", "investigation"):
        op.drop_column("incidents", col, schema=S)

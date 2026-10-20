"""reports.reports.packs: raporun kontrollerini seçen domain paketleri (T1.18)

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

S = "reports"


def upgrade() -> None:
    # Eski raporlar boş liste alır (paket bilgisi o zaman kaydedilmiyordu).
    op.add_column(
        "reports",
        sa.Column("packs", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("reports", "packs", schema=S)

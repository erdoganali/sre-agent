"""reports.decisions: karar noktalarının kayıtları (T2.5, ADR-028)

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

S = "reports"


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "report_id",
            sa.String(32),
            sa.ForeignKey(f"{S}.reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("point", sa.String(20), nullable=False),  # known | noise | group | category
        sa.Column("source", sa.String(20), nullable=False),  # model | rules | similarity
        sa.Column("effective", sa.Boolean, nullable=False),  # gölgedeki model kararı False
        sa.Column("mode", sa.String(10), nullable=False),  # off | shadow | model
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("probability", sa.Float),
        sa.Column("margin", sa.Float),
        sa.Column("probs", pg.JSONB, nullable=False),
        sa.Column("thresholds", pg.JSONB, nullable=False),
        sa.Column("method", sa.String(20)),
        sa.Column("model", sa.String(100)),
        sa.Column("taxonomy_version", sa.String(20), nullable=False),
        sa.Column("input_hash", sa.String(32), nullable=False),
        sa.Column("ref_report", sa.String(32)),
        sa.Column("reason", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("noise_flag", sa.Boolean, nullable=False, server_default=sa.false()),
        schema=S,
    )
    for col in ("report_id", "point", "created_at"):
        op.create_index(f"ix_decisions_{col}", "decisions", [col], schema=S)


def downgrade() -> None:
    op.drop_table("decisions", schema=S)

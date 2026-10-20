"""reports.incidents ve reports.reports.incident_id: olay gruplama (T3.1)

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

S = "reports"


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("team", sa.String(100)),
        sa.Column("namespaces", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("lost_nodes", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("common_anomalies", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("report_count", sa.Integer, nullable=False),
        sa.Column("label_category", sa.String(64)),
        sa.Column("label_root_cause", sa.Text),
        sa.Column("labeled_by", sa.String(100)),
        sa.Column("labeled_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_index("ix_incidents_opened_at", "incidents", ["opened_at"], schema=S)
    op.add_column("reports", sa.Column("incident_id", sa.String(32)), schema=S)
    op.add_column("reports", sa.Column("incident_reason", sa.Text), schema=S)
    op.create_index("ix_reports_incident_id", "reports", ["incident_id"], schema=S)


def downgrade() -> None:
    op.drop_index("ix_reports_incident_id", "reports", schema=S)
    op.drop_column("reports", "incident_reason", schema=S)
    op.drop_column("reports", "incident_id", schema=S)
    op.drop_table("incidents", schema=S)

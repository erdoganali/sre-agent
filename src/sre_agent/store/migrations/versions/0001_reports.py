"""reports.reports tablosu (T1.12)

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

S = "reports"


def upgrade() -> None:
    # Şemalar veritabanı kurulumunda (init betiği) açılır; burada garanti altına alınır.
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    op.execute("CREATE SCHEMA IF NOT EXISTS agent")  # Faz 3: LangGraph checkpoint'leri
    op.create_table(
        "reports",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("fingerprint", sa.String(200), nullable=False),
        sa.Column("alertname", sa.String(200), nullable=False),
        sa.Column("namespace", sa.String(200)),
        sa.Column("severity", sa.String(50)),
        sa.Column("labels", pg.JSONB, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float, nullable=False),
        sa.Column("anomalies", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("anomalies_by_category", pg.JSONB, nullable=False),
        sa.Column("blind_spots", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("is_test", sa.Boolean, nullable=False),
        sa.Column("annotation_id", sa.Integer),
        sa.Column("app_version", sa.String(50), nullable=False),
        sa.Column("taxonomy_version", sa.String(20), nullable=False),
        sa.Column("pack_versions", pg.JSONB, nullable=False),
        sa.Column("report", pg.JSONB, nullable=False),
        sa.Column("label_category", sa.String(64)),
        sa.Column("label_root_cause", sa.Text),
        sa.Column("labeled_by", sa.String(100)),
        sa.Column("labeled_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    for col in (
        "fingerprint",
        "alertname",
        "namespace",
        "generated_at",
        "is_test",
        "label_category",
    ):
        op.create_index(f"ix_reports_{col}", "reports", [col], schema=S)


def downgrade() -> None:
    op.drop_table("reports", schema=S)

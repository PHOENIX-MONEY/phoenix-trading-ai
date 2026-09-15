"""create decisions and engine_state tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candle_time", sa.String(length=64), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("signal", sa.String(length=8), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("fast_ma", sa.Float(), nullable=True),
        sa.Column("slow_ma", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("ticket", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stream_id", name="uq_decisions_stream_id"),
    )
    op.create_index(
        op.f("ix_decisions_symbol"), "decisions", ["symbol"], unique=False
    )
    op.create_index(
        op.f("ix_decisions_timestamp"), "decisions", ["processed_at"], unique=False
    )

    op.create_table(
        "engine_state",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("engine_state")
    op.drop_index(op.f("ix_decisions_timestamp"), table_name="decisions")
    op.drop_index(op.f("ix_decisions_symbol"), table_name="decisions")
    op.drop_table("decisions")
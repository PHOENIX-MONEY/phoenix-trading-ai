"""create candles table

Revision ID: 0001
Revises:
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol",
            "timeframe",
            "timestamp",
            name="uq_candles_symbol_timeframe_timestamp",
        ),
    )
    op.create_index(op.f("ix_candles_symbol"), "candles", ["symbol"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_candles_symbol"), table_name="candles")
    op.drop_table("candles")
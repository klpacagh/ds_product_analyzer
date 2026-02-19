"""add enhanced scoring columns

Revision ID: a1b2c3d4e5f6
Revises: d69585b99723
Create Date: 2026-02-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd69585b99723'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add enhanced scoring columns to trend_scores."""
    op.add_column('trend_scores', sa.Column('search_accel', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('trend_scores', sa.Column('social_velocity', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('trend_scores', sa.Column('price_fit', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('trend_scores', sa.Column('trend_shape', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('trend_scores', sa.Column('purchase_intent', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('trend_scores', sa.Column('recency', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('trend_scores', sa.Column('ad_longevity', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('trend_scores', sa.Column('review_growth', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('trend_scores', sa.Column('saturation', sa.Float(), server_default='0.0', nullable=False))


def downgrade() -> None:
    """Remove enhanced scoring columns from trend_scores."""
    op.drop_column('trend_scores', 'saturation')
    op.drop_column('trend_scores', 'review_growth')
    op.drop_column('trend_scores', 'ad_longevity')
    op.drop_column('trend_scores', 'recency')
    op.drop_column('trend_scores', 'purchase_intent')
    op.drop_column('trend_scores', 'trend_shape')
    op.drop_column('trend_scores', 'price_fit')
    op.drop_column('trend_scores', 'social_velocity')
    op.drop_column('trend_scores', 'search_accel')

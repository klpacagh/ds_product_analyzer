"""add amazon_bsr_rank and amazon_bsr_category to products

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-02-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('amazon_bsr_rank', sa.Integer(), nullable=True))
    op.add_column('products', sa.Column('amazon_bsr_category', sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column('products', 'amazon_bsr_category')
    op.drop_column('products', 'amazon_bsr_rank')

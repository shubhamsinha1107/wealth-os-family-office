"""add_lapse_threshold_to_insurance

Revision ID: 94e32adf53e0
Revises: 0daa6035686f
Create Date: 2026-05-16 17:21:09.133779

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '94e32adf53e0'
down_revision: Union[str, Sequence[str], None] = '0daa6035686f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('policies', sa.Column('lapse_after_missed_payments', sa.Integer(), nullable=True), schema='insurance')


def downgrade() -> None:
    op.drop_column('policies', 'lapse_after_missed_payments', schema='insurance')

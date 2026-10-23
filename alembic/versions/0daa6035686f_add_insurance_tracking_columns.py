"""Add insurance tracking columns

Revision ID: 0daa6035686f
Revises: 538c6b26ba82
Create Date: 2026-05-16 17:08:47.734100

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0daa6035686f'
down_revision: Union[str, Sequence[str], None] = '538c6b26ba82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create enums first
    payment_method = sa.Enum('MANUAL', 'ECS', name='paymentmethod')
    payment_method.create(op.get_bind())
    payment_status = sa.Enum('PAID', 'UNPAID', 'SKIPPED', name='paymentstatus')
    payment_status.create(op.get_bind())

    op.add_column('policies', sa.Column('file_number', sa.String(length=100), nullable=True), schema='insurance')
    op.add_column('policies', sa.Column('policy_holder_name', sa.String(length=200), nullable=True), schema='insurance')
    op.add_column('policies', sa.Column('insured_person_name', sa.String(length=200), nullable=True), schema='insurance')
    op.add_column('policies', sa.Column('fund_name', sa.String(length=200), nullable=True), schema='insurance')
    op.add_column('policies', sa.Column('fund_type', sa.String(length=100), nullable=True), schema='insurance')
    op.add_column('policies', sa.Column('premium_term', sa.Integer(), nullable=True), schema='insurance')
    op.add_column('policies', sa.Column('payment_method', sa.Enum('MANUAL', 'ECS', name='paymentmethod'), nullable=True), schema='insurance')

    op.add_column('premium_payments', sa.Column('status', sa.Enum('PAID', 'UNPAID', 'SKIPPED', name='paymentstatus'), nullable=True), schema='insurance')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('premium_payments', 'status', schema='insurance')
    op.drop_column('policies', 'payment_method', schema='insurance')
    op.drop_column('policies', 'premium_term', schema='insurance')
    op.drop_column('policies', 'fund_type', schema='insurance')
    op.drop_column('policies', 'fund_name', schema='insurance')
    op.drop_column('policies', 'insured_person_name', schema='insurance')
    op.drop_column('policies', 'policy_holder_name', schema='insurance')
    op.drop_column('policies', 'file_number', schema='insurance')

    sa.Enum(name='paymentmethod').drop(op.get_bind())
    sa.Enum(name='paymentstatus').drop(op.get_bind())

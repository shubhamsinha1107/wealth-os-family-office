"""link_insured_and_holder_to_persons

Revision ID: 26f745e66e19
Revises: 94e32adf53e0
Create Date: 2026-05-16 17:24:45.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '26f745e66e19'
down_revision: Union[str, Sequence[str], None] = '94e32adf53e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns
    op.add_column('policies', sa.Column('insured_person_id', sa.Integer(), nullable=True), schema='insurance')
    op.add_column('policies', sa.Column('holder_person_id', sa.Integer(), nullable=True), schema='insurance')
    
    # Add foreign keys
    op.create_foreign_key('fk_policy_insured_person', 'policies', 'persons', ['insured_person_id'], ['id'], source_schema='insurance', referent_schema='persons')
    op.create_foreign_key('fk_policy_holder_person', 'policies', 'persons', ['holder_person_id'], ['id'], source_schema='insurance', referent_schema='persons')
    
    # Drop old columns
    op.drop_column('policies', 'person_id', schema='insurance')
    op.drop_column('policies', 'policy_holder_name', schema='insurance')
    op.drop_column('policies', 'insured_person_name', schema='insurance')


def downgrade() -> None:
    op.add_column('policies', sa.Column('person_id', sa.Integer(), nullable=True), schema='insurance')
    op.add_column('policies', sa.Column('policy_holder_name', sa.String(200), nullable=True), schema='insurance')
    op.add_column('policies', sa.Column('insured_person_name', sa.String(200), nullable=True), schema='insurance')
    
    op.drop_constraint('fk_policy_insured_person', 'policies', schema='insurance', type_='foreignkey')
    op.drop_constraint('fk_policy_holder_person', 'policies', schema='insurance', type_='foreignkey')
    
    op.drop_column('policies', 'insured_person_id', schema='insurance')
    op.drop_column('policies', 'holder_person_id', schema='insurance')

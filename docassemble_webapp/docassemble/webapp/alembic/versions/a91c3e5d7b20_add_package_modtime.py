"""Add package modtime
Revision ID: a91c3e5d7b20
Revises: 7d2c4e1b9a60
Create Date: 2026-11-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from docassemble.base.config import dbtableprefix

# revision identifiers used by Alembic
revision = 'a91c3e5d7b20'
down_revision = '7d2c4e1b9a60'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(dbtableprefix + 'package', sa.Column('modtime', sa.DateTime()))


def downgrade():
    op.drop_column(dbtableprefix + 'package', 'modtime')
"""Add package git commit
Revision ID: f3a33ced4d95
Revises: fd1547c94c46
Create Date: 2026-08-19 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from docassemble.base.config import dbtableprefix

# revision identifiers used by Alembic
revision = 'f3a33ced4d95'
down_revision = 'fd1547c94c46'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(dbtableprefix + 'package', sa.Column('gitcommit', sa.String(255)))
    op.add_column(dbtableprefix + 'install', sa.Column('gitcommit', sa.String(255)))


def downgrade():
    op.drop_column(dbtableprefix + 'install', 'gitcommit')
    op.drop_column(dbtableprefix + 'package', 'gitcommit')
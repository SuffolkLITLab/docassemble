"""Add userdict modtime index
Revision ID: 7d2c4e1b9a60
Revises: f3a33ced4d95
Create Date: 2026-10-22 
"""
from alembic import op
import sqlalchemy as sa
from docassemble.base.config import dbtableprefix

# revision identifiers used by Alembic
revision = '7d2c4e1b9a60'
down_revision = 'f3a33ced4d95'
branch_labels = None
depends_on = None

INDEX_NAME = dbtableprefix + 'ix_userdict_modtime_indexno'
TABLE_NAME = dbtableprefix + 'userdict'


def upgrade():
    existing = [index['name'] for index in sa.inspect(op.get_bind()).get_indexes(TABLE_NAME)]
    if INDEX_NAME in existing:
        return
    # userdict can be very large, so build the index without locking the table
    with op.get_context().autocommit_block():
        op.create_index(INDEX_NAME, TABLE_NAME, ['modtime', 'indexno'], postgresql_concurrently=True)


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME, postgresql_concurrently=True)
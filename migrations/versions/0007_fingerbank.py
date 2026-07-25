"""fingerbank enrichment + settings table

Adds devices.fingerbank_device_name/fingerbank_manufacturer/
fingerbank_score/fingerbank_checked_at (app/fingerbank.py's MAC-address
lookup against Fingerbank.org's device-combinations database) and a
generic `settings` key/value table so the API key that lookup needs can
be configured by the admin from the panel (app/routers/settings.py)
instead of an env var + redeploy.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('fingerbank_device_name', sa.String(length=255), nullable=True))
    op.add_column('devices', sa.Column('fingerbank_manufacturer', sa.String(length=255), nullable=True))
    op.add_column('devices', sa.Column('fingerbank_score', sa.Integer(), nullable=True))
    op.add_column('devices', sa.Column('fingerbank_checked_at', sa.DateTime(), nullable=True))
    op.create_table(
        'settings',
        sa.Column('key', sa.String(length=64), primary_key=True),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('settings')
    op.drop_column('devices', 'fingerbank_checked_at')
    op.drop_column('devices', 'fingerbank_score')
    op.drop_column('devices', 'fingerbank_manufacturer')
    op.drop_column('devices', 'fingerbank_device_name')

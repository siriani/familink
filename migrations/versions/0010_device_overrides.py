"""device identification overrides

Adds devices.hostname_override/type_override/device_name_override --
admin-owned corrections shown in place of the auto-detected hostname/
guessed-type/Fingerbank-device-name wherever those are displayed (see
app/models.py:Device's docstring). All nullable, no backfill: existing
rows simply have no override until an admin sets one.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('hostname_override', sa.String(length=255), nullable=True))
    op.add_column('devices', sa.Column('type_override', sa.String(length=255), nullable=True))
    op.add_column('devices', sa.Column('device_name_override', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('devices', 'device_name_override')
    op.drop_column('devices', 'type_override')
    op.drop_column('devices', 'hostname_override')

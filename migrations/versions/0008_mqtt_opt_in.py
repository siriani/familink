"""mqtt opt-in per device/user

Adds devices.mqtt_enabled and users.mqtt_enabled (both default false --
see app/models.py:Device/User docstrings for why this is opt-in rather
than "publish everyone once MQTT_HOST is set", which was the previous
behavior). Also adds user_mqtt_state, the per-person counterpart to the
existing device_mqtt_state, for the new presence binary_sensor
(app/mqtt_publish.py).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('mqtt_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('mqtt_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'user_mqtt_state',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('object_id', sa.String(length=100), nullable=True),
        sa.Column('discovery_published_at', sa.DateTime(), nullable=True),
        sa.Column('last_state_published_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('user_mqtt_state')
    op.drop_column('users', 'mqtt_enabled')
    op.drop_column('devices', 'mqtt_enabled')

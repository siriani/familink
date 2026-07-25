"""drop mqtt discovery tracking

app/mqtt_publish.py no longer implements Home Assistant's MQTT Discovery
convention (homeassistant/.../config topics) -- it just publishes plain
JSON state to {prefix}/<object_id>/state, so discovery_published_at on
device_mqtt_state/user_mqtt_state is now dead: nothing sets or reads it.
last_state_published_at (kept) still does real work, tracking which rows
have a retained topic to clear if they get disabled.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('device_mqtt_state', 'discovery_published_at')
    op.drop_column('user_mqtt_state', 'discovery_published_at')


def downgrade() -> None:
    op.add_column('user_mqtt_state', sa.Column('discovery_published_at', sa.DateTime(), nullable=True))
    op.add_column('device_mqtt_state', sa.Column('discovery_published_at', sa.DateTime(), nullable=True))

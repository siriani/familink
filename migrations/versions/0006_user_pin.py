"""user pin

Adds users.pin_hash — see app/models.py:User's docstring for what this is
for (protecting the /captive "already have an account" picker, which
previously required zero verification to link a device to any existing
person). Nullable, no backfill: every existing row starts without a PIN
and is simply excluded from the /captive picker until an admin sets one
via the user's edit page (app/routers/users.py).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('pin_hash', sa.String(length=160), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'pin_hash')

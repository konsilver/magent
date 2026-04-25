"""add parse cache columns to artifacts

Revision ID: h8i9j0k1l2m3
Revises: d5e6f7g8h9i0
Create Date: 2026-04-14

Adds four columns used by the cross-turn file-reading feature:
- parsed_text: cached full-text parse output (lazy-populated on first read)
- summary: short, type-aware summary injected into prompt as "historical files"
- parsed_at: when parsed_text was last populated
- parse_error: last parse error message (to avoid repeatedly retrying broken files)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'h8i9j0k1l2m3'
down_revision: Union[str, Sequence[str], None] = 'd5e6f7g8h9i0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('artifacts', sa.Column('parsed_text', sa.Text(), nullable=True))
    op.add_column('artifacts', sa.Column('summary', sa.Text(), nullable=True))
    op.add_column('artifacts', sa.Column('parsed_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column('artifacts', sa.Column('parse_error', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('artifacts', 'parse_error')
    op.drop_column('artifacts', 'parsed_at')
    op.drop_column('artifacts', 'summary')
    op.drop_column('artifacts', 'parsed_text')

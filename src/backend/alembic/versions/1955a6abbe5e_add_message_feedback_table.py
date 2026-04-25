"""add_message_feedback_table

Revision ID: 1955a6abbe5e
Revises: 98eae3311185
Create Date: 2026-02-22 04:46:40.815562

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1955a6abbe5e'
down_revision: Union[str, Sequence[str], None] = '98eae3311185'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'message_feedback',
        sa.Column('feedback_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('message_id', sa.String(length=64), nullable=False),
        sa.Column('chat_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=True),
        sa.Column('rating', sa.String(length=10), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("rating IN ('like', 'dislike')", name='message_feedback_rating_check'),
        sa.ForeignKeyConstraint(['chat_id'], ['chat_sessions.chat_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['message_id'], ['chat_messages.message_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users_shadow.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('feedback_id'),
    )
    op.create_index('idx_message_feedback_message_id', 'message_feedback', ['message_id'])
    op.create_index('idx_message_feedback_chat_id', 'message_feedback', ['chat_id'])
    op.create_index('idx_message_feedback_user_id', 'message_feedback', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_message_feedback_user_id', table_name='message_feedback')
    op.drop_index('idx_message_feedback_chat_id', table_name='message_feedback')
    op.drop_index('idx_message_feedback_message_id', table_name='message_feedback')
    op.drop_table('message_feedback')

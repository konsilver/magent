"""Add missing KB schema: kb_chunks table, visibility, chunk_method, indexing_status columns

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """Check if a column already exists in a table."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :table AND column_name = :column"
    ), {"table": table, "column": column})
    return result.fetchone() is not None


def _table_exists(table: str) -> bool:
    """Check if a table already exists."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = :table AND table_schema = 'public'"
    ), {"table": table})
    return result.fetchone() is not None


def _constraint_exists(name: str) -> bool:
    """Check if a constraint already exists."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE constraint_name = :name"
    ), {"name": name})
    return result.fetchone() is not None


def _index_exists(name: str) -> bool:
    """Check if an index already exists."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :name"
    ), {"name": name})
    return result.fetchone() is not None


def upgrade() -> None:
    """Add missing KB columns and kb_chunks table (idempotent)."""

    # -- kb_spaces: add visibility column --
    if not _column_exists('kb_spaces', 'visibility'):
        op.add_column('kb_spaces', sa.Column(
            'visibility', sa.String(length=16), nullable=False, server_default='private'
        ))
    if not _constraint_exists('kb_spaces_visibility_check'):
        op.create_check_constraint(
            'kb_spaces_visibility_check', 'kb_spaces',
            "visibility IN ('public', 'private')"
        )
    if not _index_exists('idx_kb_spaces_visibility'):
        op.create_index('idx_kb_spaces_visibility', 'kb_spaces', ['visibility'], unique=False)

    # -- kb_spaces: add chunk_method column --
    if not _column_exists('kb_spaces', 'chunk_method'):
        op.add_column('kb_spaces', sa.Column(
            'chunk_method', sa.String(length=32), nullable=False, server_default='semantic'
        ))

    # -- kb_documents: add indexing_status column --
    if not _column_exists('kb_documents', 'indexing_status'):
        op.add_column('kb_documents', sa.Column(
            'indexing_status', sa.String(length=20), nullable=False, server_default='processing'
        ))

    # -- kb_chunks table --
    if not _table_exists('kb_chunks'):
        op.create_table('kb_chunks',
            sa.Column('chunk_id', sa.String(length=64), nullable=False),
            sa.Column('kb_id', sa.String(length=64), nullable=False),
            sa.Column('document_id', sa.String(length=64), nullable=False),
            sa.Column('chunk_index', sa.Integer(), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('questions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('char_start', sa.Integer(), nullable=True),
            sa.Column('char_end', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
            sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['kb_id'], ['kb_spaces.kb_id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['document_id'], ['kb_documents.document_id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('chunk_id')
        )
    if not _index_exists('idx_kb_chunks_kb_id'):
        op.create_index('idx_kb_chunks_kb_id', 'kb_chunks', ['kb_id'], unique=False)
    if not _index_exists('idx_kb_chunks_document_id'):
        op.create_index('idx_kb_chunks_document_id', 'kb_chunks', ['document_id'], unique=False)
    if not _index_exists('idx_kb_chunks_kb_doc'):
        op.create_index('idx_kb_chunks_kb_doc', 'kb_chunks', ['kb_id', 'document_id'], unique=False)


def downgrade() -> None:
    """Remove kb_chunks table and added columns."""
    if _index_exists('idx_kb_chunks_kb_doc'):
        op.drop_index('idx_kb_chunks_kb_doc', table_name='kb_chunks')
    if _index_exists('idx_kb_chunks_document_id'):
        op.drop_index('idx_kb_chunks_document_id', table_name='kb_chunks')
    if _index_exists('idx_kb_chunks_kb_id'):
        op.drop_index('idx_kb_chunks_kb_id', table_name='kb_chunks')
    if _table_exists('kb_chunks'):
        op.drop_table('kb_chunks')

    if _column_exists('kb_documents', 'indexing_status'):
        op.drop_column('kb_documents', 'indexing_status')
    if _column_exists('kb_spaces', 'chunk_method'):
        op.drop_column('kb_spaces', 'chunk_method')

    if _index_exists('idx_kb_spaces_visibility'):
        op.drop_index('idx_kb_spaces_visibility', table_name='kb_spaces')
    if _constraint_exists('kb_spaces_visibility_check'):
        op.drop_constraint('kb_spaces_visibility_check', 'kb_spaces', type_='check')
    if _column_exists('kb_spaces', 'visibility'):
        op.drop_column('kb_spaces', 'visibility')

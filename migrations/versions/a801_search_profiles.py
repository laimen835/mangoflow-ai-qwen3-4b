"""Candidate vectors and versioned retrieval profiles, preserving legacy index."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector
from src.core.config import settings

revision='a801searchprofiles'
down_revision='58bfa7cb42d8'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind()
    dim=bind.execute(sa.text("SELECT atttypmod FROM pg_attribute WHERE attrelid='chunks'::regclass AND attname='embedding'")).scalar_one()
    op.add_column('chunks',sa.Column('embedding_candidate',Vector(dim),nullable=True))
    op.create_index('ix_chunks_candidate_hnsw','chunks',['embedding_candidate'],postgresql_using='hnsw',
        postgresql_with={'m':16,'ef_construction':64},postgresql_ops={'embedding_candidate':'vector_cosine_ops'})
    table=op.create_table('retrieval_index_state',sa.Column('id',sa.Integer,primary_key=True),
        sa.Column('active_profile',JSONB,nullable=False),sa.Column('candidate_profile',JSONB,nullable=True))
    op.bulk_insert(table,[{'id':1,'active_profile':{'name':'legacy-v1','model':settings.embedding_model,
        'dimension':dim,'query_prefix':'','document_prefix':'','normalize':False,'version':1,'policy':'legacy'}}])


def downgrade():
    op.drop_table('retrieval_index_state')
    op.drop_index('ix_chunks_candidate_hnsw',table_name='chunks')
    op.drop_column('chunks','embedding_candidate')

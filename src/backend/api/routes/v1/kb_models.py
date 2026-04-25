"""Pydantic request/response models for Knowledge Base routes."""

from typing import List, Optional
from pydantic import BaseModel, Field


class IndexingConfig(BaseModel):
    """Indexing configuration for KB space."""
    parent_chunk_size: int = Field(1024, ge=256, le=4096, description="Parent chunk size in chars")
    child_chunk_size: int = Field(128, ge=64, le=512, description="Child chunk size in chars")
    overlap_tokens: int = Field(20, ge=0, le=100, description="Overlap between child chunks")
    parent_child_indexing: bool = Field(True, description="Enable parent-child indexing; False = index parent chunks only")
    auto_keywords_count: int = Field(0, ge=0, le=10, description="LLM auto-extract keyword count per chunk (0=disabled)")
    auto_questions_count: int = Field(0, ge=0, le=5, description="LLM auto-generate question count per chunk (0=disabled)")


class CreateKBSpaceRequest(BaseModel):
    """Request model for creating a KB space."""
    name: str = Field(..., min_length=1, max_length=255, description="KB space name")
    description: Optional[str] = Field(None, description="KB space description")
    chunk_method: Optional[str] = Field("semantic", description="Chunking strategy: semantic|laws|qa")
    metadata: Optional[dict] = Field(default_factory=dict, description="Additional metadata")
    indexing_config: Optional[IndexingConfig] = Field(None, description="Advanced indexing configuration")


class UpdateKBSpaceRequest(BaseModel):
    """Request model for updating a KB space."""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="KB space name")
    description: Optional[str] = Field(None, description="KB space description")


class PolishKBDescriptionRequest(BaseModel):
    """Request model for AI-polishing a KB description."""
    name: str = Field(..., min_length=1, max_length=255, description="KB space name")
    description: Optional[str] = Field(None, description="Current KB description")


class UpdateChunkRequest(BaseModel):
    """Request model for updating chunk tags and questions."""
    tags: Optional[List[str]] = Field(None, description="Tag list for BM25 augmentation")
    questions: Optional[List[str]] = Field(None, description="Question list for multi-surface indexing")


class ReindexRequest(BaseModel):
    """Optional indexing config override for reindexing."""
    indexing_config: Optional[IndexingConfig] = Field(None, description="Override indexing configuration")
    chunk_method: Optional[str] = Field(None, description="Override chunking method: structured|recursive|embedding_semantic|laws|qa")


class KBSpaceResponse(BaseModel):
    """Response model for KB space."""
    kb_id: str
    name: str
    description: Optional[str] = None
    document_count: int
    created_at: str
    updated_at: str


class KBDocumentResponse(BaseModel):
    """Response model for KB document."""
    document_id: str
    kb_id: str
    title: str
    filename: str
    size: int
    mime_type: str
    storage_key: str
    uploaded_at: str

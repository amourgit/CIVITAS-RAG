"""
civitas.core.models.chunk
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DocumentChunk — the indexable unit derived from a Document.

A Document is split into Chunks by the ingestion pipeline.
Each Chunk is what gets embedded and stored in the vector index.
Chunks carry a subset of their parent Document's metadata to
allow filtering at retrieval time without re-joining.

Design:
  · Chunks are derived — they do not exist without a parent Document.
  · Chunks inherit access control from their parent Document.
  · Chunk position is tracked for context window assembly.
  · Chunk content hash enables deduplication across re-ingestion.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field


class ChunkType(str, Enum):
    """How the chunk was produced."""
    SEMANTIC = "semantic"          # SemanticSplitter (LlamaIndex)
    SENTENCE = "sentence"          # SentenceSplitter
    FIXED_SIZE = "fixed_size"      # Fixed token window
    PARAGRAPH = "paragraph"        # Paragraph boundary
    PAGE = "page"                  # One page of a PDF
    SECTION = "section"            # Document section / heading
    SUMMARY = "summary"            # LLM-generated summary chunk
    TITLE = "title"                # Title / heading extraction
    TABLE = "table"                # Tabular content
    CODE = "code"                  # Code block
    CUSTOM = "custom"


class DocumentChunk(BaseModel):
    """
    Indexable unit of knowledge derived from a Document.

    Each chunk represents a coherent segment of the parent document.
    Chunks are the actual nodes stored in LlamaIndex and pgvector.

    Metadata inheritance:
      Chunks carry a flattened subset of parent Document metadata
      (domain, knowledge_space_id, access_control) to enable
      metadata filtering at retrieval time without join operations.
    """

    model_config = {"frozen": False}

    # ── Identity ───────────────────────────────────────────────
    id: UUID = Field(default_factory=uuid4, description="Unique chunk identifier")
    document_id: UUID = Field(..., description="Parent document ID")
    document_title: str = Field(..., description="Cached parent document title")
    document_version: int = Field(default=1, description="Parent document version")

    # ── Position ───────────────────────────────────────────────
    chunk_index: int = Field(..., ge=0, description="Position of this chunk within the document")
    chunk_type: ChunkType = Field(default=ChunkType.SEMANTIC)
    start_char: Optional[int] = Field(None, ge=0, description="Start character offset in source")
    end_char: Optional[int] = Field(None, ge=0, description="End character offset in source")
    page_number: Optional[int] = Field(None, ge=1, description="Source page number (for PDFs)")
    section_heading: Optional[str] = Field(None, description="Nearest section heading above this chunk")

    # ── Content ────────────────────────────────────────────────
    content: str = Field(..., min_length=1, description="Chunk text content")
    content_hash: str = Field(default="", description="SHA-256 hash of content for deduplication")
    token_count: Optional[int] = Field(None, ge=0, description="Token count (model-specific)")

    # ── Context Window ─────────────────────────────────────────
    prev_chunk_id: Optional[UUID] = Field(None, description="Previous chunk for context assembly")
    next_chunk_id: Optional[UUID] = Field(None, description="Next chunk for context assembly")
    context_prefix: Optional[str] = Field(None, description="Context from preceding chunk for overlap")

    # ── Inherited Metadata (flattened from parent) ─────────────
    # These are denormalized copies to avoid joins in the hot retrieval path
    domain: str = Field(..., description="Inherited from parent Document")
    subdomain: Optional[str] = None
    category: str = Field(..., description="Inherited from parent Document")
    knowledge_space_id: Optional[UUID] = None
    knowledge_space_name: Optional[str] = None
    taxonomy_path: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    classification_level: str = Field(default="internal")
    access_level: str = Field(default="team")
    allowed_teams: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    allowed_agent_ids: list[str] = Field(default_factory=list)

    # ── Vector ─────────────────────────────────────────────────
    embedding: Optional[list[float]] = Field(
        None, exclude=True,
        description="Dense embedding vector (not persisted in this model, managed by vector store)",
    )
    embedding_model: Optional[str] = Field(None, description="Model used to generate embedding")
    embedded_at: Optional[datetime] = None

    # ── Quality ────────────────────────────────────────────────
    quality_score: Optional[float] = Field(None, ge=0.0, le=1.0)

    # ── Custom ─────────────────────────────────────────────────
    extra_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional chunk-level metadata (e.g. table headers, code language)",
    )

    # ── Timestamps ─────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Computed ───────────────────────────────────────────────

    @computed_field
    @property
    def full_taxonomy_path(self) -> str:
        return ".".join(self.taxonomy_path)

    @computed_field
    @property
    def word_count(self) -> int:
        return len(self.content.split())

    def compute_hash(self) -> str:
        """Compute content hash for deduplication."""
        self.content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        return self.content_hash

    def to_llamaindex_node_metadata(self) -> dict[str, Any]:
        """
        Export metadata in a format suitable for LlamaIndex TextNode.
        All values must be scalars or lists of scalars for pgvector filtering.
        """
        return {
            "chunk_id": str(self.id),
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "document_version": self.document_version,
            "chunk_index": self.chunk_index,
            "chunk_type": self.chunk_type.value,
            "page_number": self.page_number,
            "section_heading": self.section_heading,
            "domain": self.domain,
            "subdomain": self.subdomain,
            "category": self.category,
            "knowledge_space_id": str(self.knowledge_space_id) if self.knowledge_space_id else None,
            "knowledge_space_name": self.knowledge_space_name,
            "taxonomy_path": self.full_taxonomy_path,
            "tags": self.tags,
            "classification_level": self.classification_level,
            "access_level": self.access_level,
            "allowed_teams": self.allowed_teams,
            "allowed_roles": self.allowed_roles,
            "allowed_agent_ids": self.allowed_agent_ids,
            "quality_score": self.quality_score,
        }

    def __repr__(self) -> str:
        return (
            f"DocumentChunk(id={self.id}, doc_id={self.document_id}, "
            f"idx={self.chunk_index}, words={self.word_count})"
        )

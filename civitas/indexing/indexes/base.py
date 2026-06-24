"""
civitas.indexing.indexes.base
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Abstract base index — defines the contract for all CIVITAS indexes.

All index types (vector, keyword, summary, graph, hierarchical)
derive from BaseKnowledgeIndex and must implement:
  · build(nodes)      — initial construction
  · add_nodes(nodes)  — incremental update
  · delete_node(id)   — node removal
  · get_retriever()   — return a LlamaIndex retriever
  · persist()         — save state to storage
  · load()            — restore from storage

IndexConfig carries all configuration and state metadata.
The IndexRegistry uses IndexConfig to track all live indexes.
"""

from __future__ import annotations

import abc
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IndexType(str, Enum):
    """Supported index types in CIVITAS."""
    VECTOR = "vector"               # Dense embedding + pgvector similarity
    KEYWORD = "keyword"             # BM25 / TF-IDF full-text search
    SUMMARY = "summary"             # Document-level LLM summaries
    KNOWLEDGE_GRAPH = "knowledge_graph"  # Entity-relation graph (GraphRAG)
    HIERARCHICAL = "hierarchical"   # Parent-child chunk hierarchy


class IndexStatus(str, Enum):
    UNINITIALIZED = "uninitialized"
    BUILDING = "building"
    READY = "ready"
    UPDATING = "updating"
    ERROR = "error"
    STALE = "stale"


class IndexConfig(BaseModel):
    """
    Configuration and runtime state of a single index.
    Persisted to the database so indexes survive restarts.
    """

    # Identity
    index_id: str = Field(default_factory=lambda: str(uuid4()))
    index_type: IndexType
    knowledge_space_id: Optional[str] = None       # Scope: None = global
    knowledge_space_name: Optional[str] = None

    # State
    status: IndexStatus = Field(default=IndexStatus.UNINITIALIZED)
    document_count: int = Field(default=0, ge=0)
    node_count: int = Field(default=0, ge=0)

    # Configuration
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimensions: int = Field(default=1536)
    similarity_metric: str = Field(default="cosine")
    chunk_size: int = Field(default=512)
    chunk_overlap: int = Field(default=64)

    # Table names (for pgvector / PostgreSQL storage)
    vector_table_name: Optional[str] = None
    keyword_table_name: Optional[str] = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    built_at: Optional[datetime] = None
    last_updated_at: Optional[datetime] = None

    # Extra
    extra: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
#  BASE INDEX
# ─────────────────────────────────────────────────────────────

class BaseKnowledgeIndex(abc.ABC):
    """
    Abstract base class for all CIVITAS knowledge indexes.

    Subclasses wrap LlamaIndex index types and expose a
    consistent interface for the IndexRegistry and retrieval layer.
    """

    def __init__(self, config: IndexConfig) -> None:
        self.config = config
        self.logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}[{config.index_id[:8]}]"
        )

    @property
    def index_id(self) -> str:
        return self.config.index_id

    @property
    def index_type(self) -> IndexType:
        return self.config.index_type

    @property
    def is_ready(self) -> bool:
        return self.config.status == IndexStatus.READY

    # ── Core Operations ────────────────────────────────────────

    @abc.abstractmethod
    def build(self, nodes: list[Any]) -> None:
        """Build the index from scratch from a list of LlamaIndex nodes."""

    @abc.abstractmethod
    def add_nodes(self, nodes: list[Any]) -> None:
        """Incrementally add nodes to an existing index."""

    @abc.abstractmethod
    def delete_node(self, node_id: str) -> None:
        """Remove a single node by its ID."""

    @abc.abstractmethod
    def get_retriever(self, **kwargs: Any) -> Any:
        """Return a configured LlamaIndex retriever for this index."""

    # ── Persistence (optional — override for persistent indexes) ──

    def persist(self) -> None:
        """Save index state. Override for indexes with in-memory state."""

    def load(self) -> None:
        """Load index state from storage. Override as needed."""

    # ── Lifecycle ──────────────────────────────────────────────

    def _mark_building(self) -> None:
        self.config.status = IndexStatus.BUILDING
        self.logger.info("Building index [%s]...", self.config.index_type.value)

    def _mark_ready(self) -> None:
        self.config.status = IndexStatus.READY
        self.config.built_at = datetime.utcnow()
        self.logger.info(
            "Index [%s] ready. Nodes: %d",
            self.config.index_type.value,
            self.config.node_count,
        )

    def _mark_error(self, exc: Exception) -> None:
        self.config.status = IndexStatus.ERROR
        self.logger.error("Index [%s] error: %s", self.config.index_type.value, exc)
        raise exc

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"id='{self.config.index_id[:8]}', "
            f"type={self.config.index_type.value}, "
            f"status={self.config.status.value}, "
            f"nodes={self.config.node_count})"
        )

"""
civitas.core.models.knowledge_space
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KnowledgeSpace — logical partition of knowledge in CIVITAS.

A KnowledgeSpace is a governed, named partition of the knowledge base.
It represents a coherent domain of knowledge accessible to specific
teams, roles, or agents under defined access rules.

Examples:
  · "legal-contracts"  — Legal team, all contracts
  · "finance-reports"  — Finance team, periodic reports
  · "hr-policies"      — HR team, internal policies
  · "global-shared"    — All authenticated users, public knowledge
  · "agent-research"   — Research agent workspace

Each KnowledgeSpace:
  · Has its own set of authorized indexes
  · Defines default retrieval strategy
  · Enforces access control at query time
  · Can be queried in isolation or combined (federated retrieval)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field


class KnowledgeSpaceType(str, Enum):
    """Classification of the knowledge space purpose."""
    DOMAIN = "domain"           # Business domain (legal, finance, hr)
    TEAM = "team"               # Team-specific workspace
    PROJECT = "project"         # Project-scoped workspace
    AGENT = "agent"             # Agent-specific knowledge store
    GLOBAL = "global"           # Organization-wide shared knowledge
    EXTERNAL = "external"       # External / third-party knowledge
    SANDBOX = "sandbox"         # Experimental / test workspace


class KnowledgeSpaceStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUSPENDED = "suspended"
    PROVISIONING = "provisioning"


class IndexStrategy(str, Enum):
    """Default indexing and retrieval strategy for the space."""
    SEMANTIC = "semantic"               # Vector search only
    KEYWORD = "keyword"                 # BM25 keyword search only
    HYBRID = "hybrid"                   # Semantic + keyword fusion
    GRAPH_ENHANCED = "graph_enhanced"   # Hybrid + GraphRAG graph context
    HIERARCHICAL = "hierarchical"       # Summary → chunk retrieval
    FULL = "full"                       # All strategies, reranked


class KnowledgeSpaceConfig(BaseModel):
    """
    Runtime configuration for a KnowledgeSpace.
    Controls indexing behavior, retrieval defaults, and quality gates.
    """
    # Index strategy
    default_index_strategy: IndexStrategy = Field(default=IndexStrategy.HYBRID)
    enabled_index_types: list[str] = Field(
        default_factory=lambda: ["vector", "keyword", "summary"],
        description="Index types to maintain for this space",
    )
    graph_rag_enabled: bool = Field(default=False, description="Enable GraphRAG for this space")

    # Retrieval defaults
    default_top_k: int = Field(default=10, ge=1, le=100)
    default_similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    reranking_enabled: bool = Field(default=True)
    reranker_model: str = Field(default="cross-encoder/ms-marco-MiniLM-L-6-v2")

    # Chunking defaults
    chunk_size: int = Field(default=512, ge=64, le=4096)
    chunk_overlap: int = Field(default=64, ge=0, le=512)
    chunk_strategy: str = Field(default="semantic")

    # Quality gates
    min_quality_score: float = Field(default=0.5, ge=0.0, le=1.0)
    auto_approve_above: float = Field(default=0.9, ge=0.0, le=1.0)
    require_review: bool = Field(default=False)

    # Embedding
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimensions: int = Field(default=1536)


class KnowledgeSpace(BaseModel):
    """
    Logical partition of the CIVITAS knowledge base.

    A KnowledgeSpace is the primary unit of organization for knowledge
    governance. Documents belong to exactly one KnowledgeSpace.
    Agents can be granted access to one or more KnowledgeSpaces.

    The space defines:
      · What knowledge it contains (via classification rules)
      · Who can access it (access control)
      · How it is indexed and retrieved (configuration)
      · Quality and governance standards (quality gates)
    """

    model_config = {"frozen": False}

    # ── Identity ───────────────────────────────────────────────
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=2, max_length=100,
                       description="Unique, URL-safe name (e.g. 'legal-contracts')")
    display_name: str = Field(..., description="Human-readable name")
    description: Optional[str] = Field(None, max_length=2000)
    slug: str = Field(..., description="URL-safe identifier derived from name")

    # ── Classification ─────────────────────────────────────────
    space_type: KnowledgeSpaceType = Field(default=KnowledgeSpaceType.DOMAIN)
    domain: Optional[str] = Field(None, description="Primary business domain")
    topics: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    # ── Status ─────────────────────────────────────────────────
    status: KnowledgeSpaceStatus = Field(default=KnowledgeSpaceStatus.PROVISIONING)

    # ── Ownership ──────────────────────────────────────────────
    owner_id: str = Field(..., description="Owner user ID")
    owner_team_id: Optional[str] = None

    # ── Access Control ─────────────────────────────────────────
    # Who can READ documents in this space
    read_teams: list[str] = Field(default_factory=list)
    read_roles: list[str] = Field(default_factory=list)
    read_agent_ids: list[str] = Field(default_factory=list)

    # Who can WRITE (ingest) into this space
    write_teams: list[str] = Field(default_factory=list)
    write_roles: list[str] = Field(default_factory=list)

    # Who can MANAGE (configure, delete) this space
    admin_user_ids: list[str] = Field(default_factory=list)

    # ── Configuration ──────────────────────────────────────────
    config: KnowledgeSpaceConfig = Field(default_factory=KnowledgeSpaceConfig)

    # ── Statistics ─────────────────────────────────────────────
    document_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    last_ingestion_at: Optional[datetime] = None
    last_query_at: Optional[datetime] = None
    total_queries: int = Field(default=0, ge=0)

    # ── Timestamps ─────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Custom ─────────────────────────────────────────────────
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    # ── Computed ───────────────────────────────────────────────

    @computed_field
    @property
    def is_active(self) -> bool:
        return self.status == KnowledgeSpaceStatus.ACTIVE

    @computed_field
    @property
    def avg_docs_per_chunk(self) -> Optional[float]:
        if self.document_count == 0:
            return None
        return round(self.chunk_count / self.document_count, 2)

    # ── Methods ────────────────────────────────────────────────

    def can_read(self, *, team_id: Optional[str] = None,
                 role: Optional[str] = None,
                 agent_id: Optional[str] = None) -> bool:
        """Check read access for a principal."""
        if not self.is_active:
            return False
        if team_id and team_id in self.read_teams:
            return True
        if role and role in self.read_roles:
            return True
        if agent_id and agent_id in self.read_agent_ids:
            return True
        return False

    def can_write(self, *, team_id: Optional[str] = None,
                  role: Optional[str] = None) -> bool:
        """Check write (ingestion) access for a principal."""
        if not self.is_active:
            return False
        if team_id and team_id in self.write_teams:
            return True
        if role and role in self.write_roles:
            return True
        return False

    def activate(self) -> None:
        """Transition space to ACTIVE status."""
        self.status = KnowledgeSpaceStatus.ACTIVE
        self.updated_at = datetime.utcnow()

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()

    def record_ingestion(self, doc_count: int = 1, chunk_count: int = 0) -> None:
        self.document_count += doc_count
        self.chunk_count += chunk_count
        self.last_ingestion_at = datetime.utcnow()
        self.touch()

    def record_query(self) -> None:
        self.total_queries += 1
        self.last_query_at = datetime.utcnow()

    def __repr__(self) -> str:
        return f"KnowledgeSpace(name='{self.name}', docs={self.document_count}, status={self.status})"

"""
civitas.core.models.metadata
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Enterprise-grade document metadata model.

This module defines the complete metadata schema for any document
ingested into the CIVITAS knowledge platform.

The metadata model is the backbone of:
  · Classification and discovery
  · Access control and security
  · Lifecycle management
  · Quality governance
  · Audit and compliance
  · Retrieval filtering

Design principles:
  · All fields typed — no raw dicts for structured data
  · Immutable taxonomy path — classification is authoritative
  · Computed quality composite — single score derived from sub-scores
  · Soft-delete lifecycle — documents are never truly deleted
  · Extensible via custom_fields — domain-specific data without schema changes
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_validator


# ─────────────────────────────────────────────────────────────
#  ENUMERATIONS
# ─────────────────────────────────────────────────────────────

class SourceType(str, Enum):
    """Origin of the document."""
    FILESYSTEM = "filesystem"
    DATABASE = "database"
    API = "api"
    MANUAL_UPLOAD = "manual_upload"
    WEB_CRAWL = "web_crawl"
    EMAIL = "email"
    STREAM = "stream"
    GENERATED = "generated"      # AI-generated summaries, reports
    UNKNOWN = "unknown"


class ClassificationLevel(str, Enum):
    """
    Information security classification.
    Aligned with standard enterprise information security frameworks.
    """
    PUBLIC = "public"              # Freely shareable externally
    INTERNAL = "internal"          # Internal use only
    CONFIDENTIAL = "confidential"  # Need-to-know basis
    RESTRICTED = "restricted"      # Highly sensitive, named individuals only
    TOP_SECRET = "top_secret"      # Maximum restriction (regulatory / legal)


class AccessLevel(str, Enum):
    """
    Granularity of access control applied to this document.
    Determines which resolver is used at query time.
    """
    OPEN = "open"              # Any authenticated user
    SPACE = "space"            # Any user in the knowledge space
    TEAM = "team"              # Specific teams listed in allowed_teams
    ROLE = "role"              # Specific roles listed in allowed_roles
    INDIVIDUAL = "individual"  # Specific user IDs
    AGENT = "agent"            # Only specific agent IDs may retrieve this
    SYSTEM = "system"          # Internal system access only


class DocumentLifecycleState(str, Enum):
    """
    Canonical lifecycle states of a document.
    Transition diagram:
      DRAFT → PENDING_REVIEW → IN_REVIEW → APPROVED → ACTIVE
      ACTIVE → DEPRECATED → ARCHIVED
      Any state → REJECTED (back to DRAFT or removed)
      Any state → PURGED (hard delete, audit only)
    """
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    REJECTED = "rejected"
    PURGED = "purged"           # Soft-delete: metadata kept, content removed


class RetentionPolicy(str, Enum):
    """Standard retention policies."""
    SHORT_TERM = "short_term"       # 90 days
    STANDARD = "standard"           # 1 year
    LONG_TERM = "long_term"         # 5 years
    PERMANENT = "permanent"         # Never expire
    REGULATORY = "regulatory"       # Defined by compliance team
    CUSTOM = "custom"               # Defined in retention_until field


# ─────────────────────────────────────────────────────────────
#  SUPPORTING STRUCTURES
# ─────────────────────────────────────────────────────────────

class DocumentOwnership(BaseModel):
    """Ownership and attribution metadata."""
    owner_id: str = Field(..., description="User ID of the document owner")
    owner_name: Optional[str] = Field(None, description="Human-readable owner name")
    author: Optional[str] = Field(None, description="Original author if different from owner")
    contributors: list[str] = Field(
        default_factory=list,
        description="Additional contributors",
    )
    team_id: Optional[str] = Field(None, description="Owning team identifier")
    team_name: Optional[str] = Field(None, description="Owning team name")
    department: Optional[str] = Field(None, description="Owning department")
    cost_center: Optional[str] = Field(None, description="Cost center for billing/chargeback")


class DocumentSource(BaseModel):
    """Origin and provenance of the document."""
    source_type: SourceType = Field(default=SourceType.UNKNOWN)
    source_url: Optional[str] = Field(None, description="URL if sourced from web/API")
    source_path: Optional[str] = Field(None, description="Filesystem path if sourced locally")
    source_system: Optional[str] = Field(None, description="Source system name (e.g. 'SharePoint')")
    original_filename: Optional[str] = Field(None, description="Filename before ingestion")
    connector_id: Optional[str] = Field(None, description="Connector used for ingestion")
    ingested_by: Optional[str] = Field(None, description="User or system that triggered ingestion")


class DocumentQuality(BaseModel):
    """
    Multi-dimensional quality assessment.

    Scores range from 0.0 (worst) to 1.0 (best).
    Each dimension captures a different quality axis:
      - completeness: does it have enough content to be useful?
      - freshness: how recent / up-to-date is the information?
      - consistency: is the document internally consistent?
      - relevance: how relevant is it to its declared knowledge space?
    """
    quality_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    completeness_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    freshness_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    consistency_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    relevance_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    quality_issues: list[str] = Field(
        default_factory=list,
        description="List of detected quality issues",
    )
    quality_checked_at: Optional[datetime] = None
    quality_checked_by: Optional[str] = None   # 'system' or user ID

    @computed_field
    @property
    def composite_score(self) -> Optional[float]:
        """Weighted average of all available quality dimensions."""
        weights = {
            "quality_score": 0.30,
            "completeness_score": 0.25,
            "freshness_score": 0.20,
            "consistency_score": 0.15,
            "relevance_score": 0.10,
        }
        total_weight = 0.0
        total_score = 0.0
        for field_name, weight in weights.items():
            val = getattr(self, field_name)
            if val is not None:
                total_score += val * weight
                total_weight += weight
        if total_weight == 0.0:
            return None
        return round(total_score / total_weight, 4)

    @computed_field
    @property
    def passes_minimum_threshold(self) -> bool:
        """True if composite score meets the minimum acceptable quality bar."""
        score = self.composite_score
        return score is not None and score >= 0.5


class DocumentAccessControl(BaseModel):
    """Fine-grained access control configuration."""
    classification_level: ClassificationLevel = Field(
        default=ClassificationLevel.INTERNAL,
    )
    access_level: AccessLevel = Field(default=AccessLevel.TEAM)

    # Whitelists (only relevant when access_level matches)
    allowed_teams: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    allowed_user_ids: list[str] = Field(default_factory=list)
    allowed_agent_ids: list[str] = Field(default_factory=list)

    # Explicit denials (override any whitelist)
    denied_teams: list[str] = Field(default_factory=list)
    denied_user_ids: list[str] = Field(default_factory=list)

    def can_access(self, *, team_id: Optional[str] = None,
                   role: Optional[str] = None,
                   user_id: Optional[str] = None,
                   agent_id: Optional[str] = None) -> bool:
        """
        Evaluate access rights for a given principal.
        Returns True if access is permitted.
        """
        # Explicit denials always win
        if team_id and team_id in self.denied_teams:
            return False
        if user_id and user_id in self.denied_user_ids:
            return False

        match self.access_level:
            case AccessLevel.OPEN | AccessLevel.SPACE:
                return True
            case AccessLevel.TEAM:
                return team_id is not None and team_id in self.allowed_teams
            case AccessLevel.ROLE:
                return role is not None and role in self.allowed_roles
            case AccessLevel.INDIVIDUAL:
                return user_id is not None and user_id in self.allowed_user_ids
            case AccessLevel.AGENT:
                return agent_id is not None and agent_id in self.allowed_agent_ids
            case AccessLevel.SYSTEM:
                return False   # No external access
            case _:
                return False


class DocumentProcessingInfo(BaseModel):
    """Tracks how this document was processed by the CIVITAS pipeline."""
    ingestion_pipeline: str = Field(default="default", description="Pipeline version/name")
    chunk_strategy: str = Field(default="semantic", description="Chunking strategy used")
    embedding_model: str = Field(default="text-embedding-3-small")
    index_ids: list[str] = Field(
        default_factory=list,
        description="IDs of all indexes where this document appears",
    )
    chunk_count: int = Field(default=0, ge=0)
    processed_at: Optional[datetime] = None
    processing_duration_ms: Optional[int] = None
    processing_errors: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────
#  MAIN METADATA MODEL
# ─────────────────────────────────────────────────────────────

class DocumentMetadata(BaseModel):
    """
    Enterprise-grade metadata model for CIVITAS documents.

    This is the single source of truth for all non-content information
    about a document. It governs classification, access, lifecycle,
    quality, and processing.

    Usage:
        meta = DocumentMetadata(
            domain="legal",
            category="contract",
            taxonomy_path=["legal", "contracts", "service_agreements"],
            knowledge_space_id=UUID("..."),
            knowledge_space_name="legal-contracts",
            ownership=DocumentOwnership(owner_id="user-123", team_id="legal-team"),
            source=DocumentSource(source_type=SourceType.FILESYSTEM, original_filename="MSA_2024.pdf"),
            access_control=DocumentAccessControl(
                classification_level=ClassificationLevel.CONFIDENTIAL,
                access_level=AccessLevel.TEAM,
                allowed_teams=["legal-team", "executive"],
            ),
        )
    """

    model_config = {"frozen": False}

    # ── Classification ─────────────────────────────────────────
    domain: str = Field(..., description="Top-level business domain (e.g. 'legal', 'finance')")
    subdomain: Optional[str] = Field(None, description="Second-level domain subdivision")
    category: str = Field(..., description="Document category (e.g. 'contract', 'invoice')")
    subcategory: Optional[str] = None
    taxonomy_path: list[str] = Field(
        default_factory=list,
        description="Ordered path in the taxonomy tree, e.g. ['legal', 'contracts', 'service_agreements']",
    )
    tags: list[str] = Field(default_factory=list, description="Free-form searchable tags")
    keywords: list[str] = Field(default_factory=list, description="Extracted or assigned keywords")
    topics: list[str] = Field(default_factory=list, description="High-level topic labels")

    # ── Knowledge Space ────────────────────────────────────────
    knowledge_space_id: Optional[UUID] = Field(
        None, description="ID of the knowledge space this document belongs to",
    )
    knowledge_space_name: Optional[str] = Field(
        None, description="Human-readable knowledge space name",
    )

    # ── Temporal ───────────────────────────────────────────────
    document_date: Optional[date] = Field(
        None, description="The intrinsic date of the document itself",
    )
    effective_date: Optional[date] = Field(None, description="Date from which the document is valid")
    expiry_date: Optional[date] = Field(None, description="Date after which the document is no longer valid")
    review_date: Optional[date] = Field(None, description="Next scheduled review date")

    # ── Ownership ──────────────────────────────────────────────
    ownership: DocumentOwnership = Field(default_factory=lambda: DocumentOwnership(owner_id="system"))

    # ── Source ─────────────────────────────────────────────────
    source: DocumentSource = Field(default_factory=DocumentSource)

    # ── Quality ────────────────────────────────────────────────
    quality: DocumentQuality = Field(default_factory=DocumentQuality)

    # ── Lifecycle ──────────────────────────────────────────────
    lifecycle_state: DocumentLifecycleState = Field(default=DocumentLifecycleState.DRAFT)
    retention_policy: RetentionPolicy = Field(default=RetentionPolicy.STANDARD)
    retention_until: Optional[date] = None
    reviewed_by: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    deprecated_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None

    # ── Access Control ─────────────────────────────────────────
    access_control: DocumentAccessControl = Field(default_factory=DocumentAccessControl)

    # ── Processing ─────────────────────────────────────────────
    processing: DocumentProcessingInfo = Field(default_factory=DocumentProcessingInfo)

    # ── Inter-document Relationships ───────────────────────────
    related_document_ids: list[UUID] = Field(
        default_factory=list, description="Documents that are related to this one",
    )
    supersedes_id: Optional[UUID] = Field(
        None, description="Document ID that this document replaces",
    )
    superseded_by_id: Optional[UUID] = Field(
        None, description="Document ID that supersedes this document",
    )
    parent_collection_id: Optional[UUID] = Field(
        None, description="ID of the collection this document belongs to",
    )

    # ── Audit ──────────────────────────────────────────────────
    last_accessed_at: Optional[datetime] = None
    access_count: int = Field(default=0, ge=0)
    last_retrieved_by: Optional[str] = None

    # ── Custom / Domain-specific ───────────────────────────────
    custom_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific metadata without schema changes",
    )

    # ── Computed Properties ────────────────────────────────────

    @computed_field
    @property
    def is_expired(self) -> bool:
        """True if the document has passed its expiry date."""
        if self.expiry_date:
            return date.today() > self.expiry_date
        return False

    @computed_field
    @property
    def days_until_expiry(self) -> Optional[int]:
        """Days remaining before expiry. Negative means already expired."""
        if self.expiry_date:
            return (self.expiry_date - date.today()).days
        return None

    @computed_field
    @property
    def is_active(self) -> bool:
        """True if the document is in an active, accessible lifecycle state."""
        return self.lifecycle_state in {
            DocumentLifecycleState.APPROVED,
            DocumentLifecycleState.ACTIVE,
        }

    @computed_field
    @property
    def full_taxonomy_path(self) -> str:
        """Dot-separated taxonomy path for indexing and filtering."""
        return ".".join(self.taxonomy_path)

    @field_validator("taxonomy_path")
    @classmethod
    def validate_taxonomy_path(cls, v: list[str]) -> list[str]:
        """Ensure taxonomy path elements are lowercase, no spaces."""
        return [p.lower().replace(" ", "_") for p in v]

    def record_access(self, user_or_agent_id: str) -> None:
        """Update access tracking fields."""
        self.last_accessed_at = datetime.utcnow()
        self.access_count += 1
        self.last_retrieved_by = user_or_agent_id

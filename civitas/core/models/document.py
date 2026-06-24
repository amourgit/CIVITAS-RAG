"""
civitas.core.models.document
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Core Document entity — the atomic unit of knowledge in CIVITAS.

A Document represents any piece of information regardless of format.
It is immutable once ACTIVE (mutations create new versions).
All knowledge operations (indexing, retrieval, governance) operate
on Document entities or their derived chunks.

Version history is maintained via the version field + parent_version_id.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

from civitas.core.models.metadata import DocumentMetadata


# ─────────────────────────────────────────────────────────────
#  ENUMERATIONS
# ─────────────────────────────────────────────────────────────

class DocumentFormat(str, Enum):
    """Supported document formats."""
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    HTML = "html"
    MARKDOWN = "markdown"
    TEXT = "text"
    JSON = "json"
    CSV = "csv"
    XML = "xml"
    EMAIL = "email"
    IMAGE = "image"       # OCR'd image
    AUDIO = "audio"       # Transcribed audio
    UNKNOWN = "unknown"


class DocumentLanguage(str, Enum):
    """Supported document languages (ISO 639-1)."""
    FRENCH = "fr"
    ENGLISH = "en"
    SPANISH = "es"
    GERMAN = "de"
    ITALIAN = "it"
    PORTUGUESE = "pt"
    ARABIC = "ar"
    DUTCH = "nl"
    CHINESE = "zh"
    JAPANESE = "ja"
    UNKNOWN = "unknown"


# ─────────────────────────────────────────────────────────────
#  DOCUMENT ENTITY
# ─────────────────────────────────────────────────────────────

class Document(BaseModel):
    """
    Core knowledge unit in the CIVITAS platform.

    A Document goes through the following lifecycle:
      1. Created (DRAFT) — raw file received, basic metadata populated
      2. Parsed       — content extracted from binary format
      3. Enriched     — metadata enriched (language, keywords, classification)
      4. Chunked      — content split into indexable chunks
      5. Indexed      — chunks embedded and stored in vector/keyword/graph indexes
      6. Active       — accessible for retrieval by agents

    Documents are never hard-deleted. The lifecycle_state transitions
    to DEPRECATED → ARCHIVED → PURGED (content removed, metadata kept).

    Versioning: every mutation to an ACTIVE document creates a new Document
    with version = old.version + 1 and parent_version_id = old.id.
    """

    model_config = {"frozen": False, "arbitrary_types_allowed": True}

    # ── Identity ───────────────────────────────────────────────
    id: UUID = Field(default_factory=uuid4, description="Unique document identifier (CIVITAS internal)")
    external_id: Optional[str] = Field(
        None, description="Identifier from the source system (e.g. SharePoint doc ID)",
    )
    title: str = Field(..., min_length=1, max_length=1000, description="Document title")
    slug: Optional[str] = Field(None, description="URL-safe short identifier")

    # ── Versioning ─────────────────────────────────────────────
    version: int = Field(default=1, ge=1, description="Document version number")
    parent_version_id: Optional[UUID] = Field(
        None, description="ID of the previous version, if this is a revision",
    )
    is_latest_version: bool = Field(default=True)

    # ── Content ────────────────────────────────────────────────
    content: Optional[str] = Field(None, description="Extracted plain-text content")
    content_preview: Optional[str] = Field(None, max_length=1000, description="First N chars preview")
    raw_content_path: Optional[str] = Field(
        None, description="Path to binary raw content in document store",
    )
    checksum: Optional[str] = Field(None, description="SHA-256 hash of content")

    # ── Format & Size ──────────────────────────────────────────
    format: DocumentFormat = Field(default=DocumentFormat.UNKNOWN)
    language: DocumentLanguage = Field(default=DocumentLanguage.UNKNOWN)
    encoding: str = Field(default="utf-8")
    byte_size: Optional[int] = Field(None, ge=0, description="Size in bytes")
    page_count: Optional[int] = Field(None, ge=0)
    word_count: Optional[int] = Field(None, ge=0)
    character_count: Optional[int] = Field(None, ge=0)

    # ── Metadata ───────────────────────────────────────────────
    metadata: DocumentMetadata = Field(
        ..., description="Enterprise governance and classification metadata",
    )

    # ── Chunk References ───────────────────────────────────────
    chunk_ids: list[UUID] = Field(
        default_factory=list,
        description="IDs of DocumentChunks derived from this document",
    )

    # ── Timestamps ─────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    indexed_at: Optional[datetime] = Field(None, description="When the document was last indexed")

    # ── Computed ───────────────────────────────────────────────

    @computed_field
    @property
    def knowledge_space_id(self) -> Optional[UUID]:
        """Shortcut to metadata.knowledge_space_id."""
        return self.metadata.knowledge_space_id

    @computed_field
    @property
    def domain(self) -> str:
        """Shortcut to metadata.domain."""
        return self.metadata.domain

    @computed_field
    @property
    def is_indexed(self) -> bool:
        """True if this document has been indexed at least once."""
        return self.indexed_at is not None

    @computed_field
    @property
    def has_content(self) -> bool:
        """True if the document has extracted text content."""
        return bool(self.content and self.content.strip())

    # ── Lifecycle Methods ──────────────────────────────────────

    def compute_checksum(self) -> str:
        """Compute and store SHA-256 checksum of content."""
        if not self.content:
            return ""
        self.checksum = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        return self.checksum

    def set_content(self, raw_text: str) -> None:
        """Set document content and compute derived fields."""
        self.content = raw_text
        self.content_preview = raw_text[:500] if raw_text else None
        self.word_count = len(raw_text.split()) if raw_text else 0
        self.character_count = len(raw_text) if raw_text else 0
        self.compute_checksum()
        self.touch()

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.utcnow()

    def mark_indexed(self) -> None:
        """Record that this document has been successfully indexed."""
        self.indexed_at = datetime.utcnow()
        self.touch()

    def create_new_version(self) -> "Document":
        """
        Create a new version of this document.
        The current version is superseded by the new one.
        """
        self.is_latest_version = False
        self.touch()

        new_doc = self.model_copy(deep=True)
        new_doc.id = uuid4()
        new_doc.version = self.version + 1
        new_doc.parent_version_id = self.id
        new_doc.is_latest_version = True
        new_doc.chunk_ids = []
        new_doc.indexed_at = None
        new_doc.created_at = datetime.utcnow()
        new_doc.updated_at = datetime.utcnow()
        return new_doc

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Document":
        """Deserialize from dictionary."""
        return cls.model_validate(data)

    @model_validator(mode="after")
    def _derive_preview(self) -> "Document":
        """Auto-populate content_preview if not set."""
        if self.content and not self.content_preview:
            self.content_preview = self.content[:500]
        return self

    def __repr__(self) -> str:
        return (
            f"Document(id={self.id}, title='{self.title[:40]}...', "
            f"domain='{self.domain}', v={self.version})"
        )

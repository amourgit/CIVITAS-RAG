"""
civitas.governance.audit.events
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Typed audit event definitions for the CIVITAS platform.

Every significant action emits an AuditEvent.
Events are immutable records — never updated, only appended.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AuditEventType(str, Enum):
    # Document events
    DOCUMENT_INGESTED = "document.ingested"
    DOCUMENT_UPDATED = "document.updated"
    DOCUMENT_DELETED = "document.deleted"
    DOCUMENT_PURGED = "document.purged"
    DOCUMENT_LIFECYCLE_CHANGED = "document.lifecycle.changed"
    DOCUMENT_QUALITY_CHECKED = "document.quality.checked"
    DOCUMENT_QUALITY_REJECTED = "document.quality.rejected"

    # Access events
    DOCUMENT_ACCESSED = "document.accessed"
    DOCUMENT_RETRIEVED = "document.retrieved"
    DOCUMENT_ACCESS_DENIED = "document.access.denied"

    # Index events
    INDEX_BUILT = "index.built"
    INDEX_UPDATED = "index.updated"
    INDEX_DELETED = "index.deleted"
    INDEX_REBUILD_TRIGGERED = "index.rebuild.triggered"

    # Knowledge space events
    SPACE_CREATED = "space.created"
    SPACE_UPDATED = "space.updated"
    SPACE_ARCHIVED = "space.archived"
    SPACE_QUERIED = "space.queried"

    # Governance events
    POLICY_APPLIED = "policy.applied"
    RETENTION_TRIGGERED = "retention.triggered"

    # System events
    PIPELINE_STARTED = "pipeline.started"
    PIPELINE_COMPLETED = "pipeline.completed"
    PIPELINE_FAILED = "pipeline.failed"


class AuditEvent(BaseModel):
    """
    Immutable audit event record.

    Every significant platform action emits one AuditEvent.
    Events are append-only — never modified after creation.
    """

    model_config = {"frozen": True}

    event_id: UUID = Field(default_factory=uuid4)
    event_type: AuditEventType
    occurred_at: datetime = Field(default_factory=datetime.utcnow)

    # Principal (who)
    actor_id: str = Field(..., description="User ID, agent ID, or 'system'")
    actor_type: str = Field(default="system")   # user | agent | system

    # Resource (what)
    resource_type: str = Field(..., description="e.g. 'document', 'index', 'space'")
    resource_id: Optional[str] = None
    resource_name: Optional[str] = None

    # Knowledge space context
    knowledge_space_id: Optional[str] = None
    knowledge_space_name: Optional[str] = None

    # Payload
    payload: dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None

    # Outcome
    success: bool = True
    error_message: Optional[str] = None

    # Tracing
    correlation_id: Optional[str] = None   # For grouping related events
    session_id: Optional[str] = None

    def to_log_line(self) -> str:
        return (
            f"[AUDIT] {self.occurred_at.isoformat()} "
            f"{self.event_type.value} "
            f"actor={self.actor_id} "
            f"resource={self.resource_type}:{self.resource_id or '-'} "
            f"success={self.success}"
            + (f" error={self.error_message}" if self.error_message else "")
        )

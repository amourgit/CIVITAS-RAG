"""
civitas.governance.audit.logger
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AuditLogger — structured, append-only audit trail.

Every significant CIVITAS action emits an AuditEvent.
The logger persists events to:
  · PostgreSQL (primary — queryable, durable)
  · Python logging (secondary — for log aggregation pipelines)

The AuditLogger is injected into:
  · IngestionPipeline
  · LifecycleManager
  · RetrievalEngine
  · IndexRegistry (on rebuild)
  · KnowledgeSpaceRegistry (on mutations)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from civitas.governance.audit.events import AuditEvent, AuditEventType

logger = logging.getLogger("civitas.audit")


class AuditLogger:
    """
    Append-only audit trail for the CIVITAS platform.

    Usage:
        audit = AuditLogger(db_session=session)
        audit.log(
            event_type=AuditEventType.DOCUMENT_INGESTED,
            actor_id="user-123",
            resource_type="document",
            resource_id=str(doc.id),
            payload={"domain": doc.domain, "chunks": 12},
        )
    """

    def __init__(self, db_session: Optional[Any] = None) -> None:
        self.db_session = db_session   # SQLAlchemy session (optional)

    # ── Core Log Method ────────────────────────────────────────

    def log(
        self,
        event_type: AuditEventType,
        actor_id: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        resource_name: Optional[str] = None,
        knowledge_space_id: Optional[str] = None,
        knowledge_space_name: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        message: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        actor_type: str = "system",
        correlation_id: Optional[str] = None,
    ) -> AuditEvent:
        """Create and persist a single audit event."""
        event = AuditEvent(
            event_type=event_type,
            actor_id=actor_id,
            actor_type=actor_type,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            knowledge_space_id=knowledge_space_id,
            knowledge_space_name=knowledge_space_name,
            payload=payload or {},
            message=message,
            success=success,
            error_message=error_message,
            correlation_id=correlation_id,
        )
        self._emit(event)
        return event

    # ── Convenience Methods ────────────────────────────────────

    def log_ingestion(
        self,
        document_id: UUID,
        document_title: str,
        actor_id: str,
        knowledge_space_name: str,
        chunk_count: int,
        quality_score: Optional[float] = None,
        duration_ms: Optional[int] = None,
    ) -> AuditEvent:
        return self.log(
            event_type=AuditEventType.DOCUMENT_INGESTED,
            actor_id=actor_id,
            resource_type="document",
            resource_id=str(document_id),
            resource_name=document_title,
            knowledge_space_name=knowledge_space_name,
            payload={
                "chunk_count": chunk_count,
                "quality_score": quality_score,
                "duration_ms": duration_ms,
            },
        )

    def log_lifecycle_transition(
        self,
        document_id: UUID,
        from_state: str,
        to_state: str,
        actor: str,
        reason: Optional[str] = None,
    ) -> AuditEvent:
        return self.log(
            event_type=AuditEventType.DOCUMENT_LIFECYCLE_CHANGED,
            actor_id=actor,
            resource_type="document",
            resource_id=str(document_id),
            payload={
                "from_state": from_state,
                "to_state": to_state,
                "reason": reason,
            },
        )

    def log_retrieval(
        self,
        query: str,
        knowledge_space_name: str,
        results_count: int,
        latency_ms: int,
        strategies: list[str],
        requesting_principal: Optional[str] = None,
    ) -> AuditEvent:
        return self.log(
            event_type=AuditEventType.SPACE_QUERIED,
            actor_id=requesting_principal or "anonymous",
            resource_type="knowledge_space",
            resource_name=knowledge_space_name,
            knowledge_space_name=knowledge_space_name,
            payload={
                "query_preview": query[:100],
                "results_count": results_count,
                "latency_ms": latency_ms,
                "strategies": strategies,
            },
        )

    def log_access_denied(
        self,
        principal_id: str,
        document_id: str,
        reason: str,
    ) -> AuditEvent:
        return self.log(
            event_type=AuditEventType.DOCUMENT_ACCESS_DENIED,
            actor_id=principal_id,
            resource_type="document",
            resource_id=document_id,
            success=False,
            error_message=reason,
        )

    def log_quality_rejection(
        self,
        document_id: UUID,
        score: float,
        threshold: float,
        issues: list[str],
    ) -> AuditEvent:
        return self.log(
            event_type=AuditEventType.DOCUMENT_QUALITY_REJECTED,
            actor_id="system:quality-checker",
            resource_type="document",
            resource_id=str(document_id),
            success=False,
            payload={
                "score": score,
                "threshold": threshold,
                "issues": issues,
            },
        )

    def log_index_built(
        self,
        index_id: str,
        index_type: str,
        space_name: str,
        node_count: int,
        duration_ms: int,
    ) -> AuditEvent:
        return self.log(
            event_type=AuditEventType.INDEX_BUILT,
            actor_id="system:indexer",
            resource_type="index",
            resource_id=index_id,
            knowledge_space_name=space_name,
            payload={
                "index_type": index_type,
                "node_count": node_count,
                "duration_ms": duration_ms,
            },
        )

    # ── Persistence ────────────────────────────────────────────

    def _emit(self, event: AuditEvent) -> None:
        """Persist audit event and write to structured log."""
        # 1. Structured Python log (always)
        log_fn = logger.info if event.success else logger.warning
        log_fn(event.to_log_line(), extra={"audit_event": event.model_dump(mode="json")})

        # 2. PostgreSQL persistence (if session available)
        if self.db_session:
            self._persist_to_db(event)

    def _persist_to_db(self, event: AuditEvent) -> None:
        """Insert audit event into the audit_events PostgreSQL table."""
        try:
            from sqlalchemy import text
            self.db_session.execute(
                text("""
                    INSERT INTO civitas_audit_events (
                        event_id, event_type, occurred_at,
                        actor_id, actor_type,
                        resource_type, resource_id, resource_name,
                        knowledge_space_id, knowledge_space_name,
                        payload, message, success, error_message, correlation_id
                    ) VALUES (
                        :event_id, :event_type, :occurred_at,
                        :actor_id, :actor_type,
                        :resource_type, :resource_id, :resource_name,
                        :knowledge_space_id, :knowledge_space_name,
                        :payload::jsonb, :message, :success, :error_message, :correlation_id
                    )
                """),
                {
                    "event_id": str(event.event_id),
                    "event_type": event.event_type.value,
                    "occurred_at": event.occurred_at,
                    "actor_id": event.actor_id,
                    "actor_type": event.actor_type,
                    "resource_type": event.resource_type,
                    "resource_id": event.resource_id,
                    "resource_name": event.resource_name,
                    "knowledge_space_id": event.knowledge_space_id,
                    "knowledge_space_name": event.knowledge_space_name,
                    "payload": __import__("json").dumps(event.payload),
                    "message": event.message,
                    "success": event.success,
                    "error_message": event.error_message,
                    "correlation_id": event.correlation_id,
                },
            )
            self.db_session.commit()
        except Exception as exc:
            logger.error("Failed to persist audit event to DB: %s", exc)
            # Never raise from audit — audit failures must not block operations

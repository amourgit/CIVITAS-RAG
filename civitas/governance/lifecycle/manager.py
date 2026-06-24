"""
civitas.governance.lifecycle.manager
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LifecycleManager — enforces document lifecycle transitions.

All state changes to a Document must go through the LifecycleManager.
It enforces:
  · Valid transition rules (state machine)
  · Mandatory metadata updates (timestamps, actor)
  · Audit trail emission
  · Index update requests (deprecate → remove from indexes)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from civitas.core.exceptions.knowledge_exceptions import LifecycleTransitionError
from civitas.core.models.document import Document
from civitas.core.models.metadata import DocumentLifecycleState as State
from civitas.governance.lifecycle.states import (
    ALLOWED_TRANSITIONS,
    can_retrieve,
    is_transition_allowed,
    requires_versioning,
)

logger = logging.getLogger(__name__)


class LifecycleManager:
    """
    Governs all document lifecycle state transitions.

    Usage:
        manager = LifecycleManager(audit_logger=audit_logger)
        manager.transition(document, State.ACTIVE, actor="admin-user")
    """

    def __init__(self, audit_logger: Optional[object] = None) -> None:
        self.audit_logger = audit_logger

    def transition(
        self,
        document: Document,
        to_state: State,
        actor: str,
        reason: Optional[str] = None,
    ) -> Document:
        """
        Transition a document to a new lifecycle state.

        Validates the transition, updates metadata, emits audit event.
        Returns the updated document.
        """
        from_state = document.metadata.lifecycle_state

        if from_state == to_state:
            logger.debug("Lifecycle no-op: document %s already in state %s", document.id, to_state)
            return document

        if not is_transition_allowed(from_state, to_state):
            raise LifecycleTransitionError(document.id, from_state.value, to_state.value)

        logger.info(
            "Lifecycle transition: document=%s %s → %s by %s",
            document.id, from_state.value, to_state.value, actor,
        )

        # Apply state-specific side effects
        now = datetime.utcnow()
        meta = document.metadata

        match to_state:
            case State.IN_REVIEW:
                meta.reviewed_by = actor
            case State.APPROVED:
                meta.approved_by = actor
                meta.approved_at = now
            case State.ACTIVE:
                meta.lifecycle_state = State.ACTIVE
            case State.DEPRECATED:
                meta.deprecated_at = now
            case State.ARCHIVED:
                meta.archived_at = now
            case State.PURGED:
                # Purge: clear content, keep metadata (soft-delete)
                document.content = None
                document.content_preview = None
                document.chunk_ids = []

        meta.lifecycle_state = to_state
        document.touch()

        # Emit audit event
        self._emit_audit(document, from_state, to_state, actor, reason)

        return document

    def submit_for_review(self, document: Document, submitter: str) -> Document:
        return self.transition(document, State.PENDING_REVIEW, actor=submitter,
                               reason="Submitted for review")

    def approve(self, document: Document, approver: str) -> Document:
        return self.transition(document, State.APPROVED, actor=approver, reason="Approved")

    def activate(self, document: Document, actor: str = "system") -> Document:
        return self.transition(document, State.ACTIVE, actor=actor, reason="Activated")

    def deprecate(self, document: Document, actor: str, reason: Optional[str] = None) -> Document:
        return self.transition(document, State.DEPRECATED, actor=actor, reason=reason)

    def archive(self, document: Document, actor: str) -> Document:
        return self.transition(document, State.ARCHIVED, actor=actor, reason="Archived")

    def reject(self, document: Document, reviewer: str, reason: Optional[str] = None) -> Document:
        return self.transition(document, State.REJECTED, actor=reviewer, reason=reason)

    def purge(self, document: Document, admin: str, reason: Optional[str] = None) -> Document:
        """Soft-delete: content removed, metadata retained for audit."""
        return self.transition(document, State.PURGED, actor=admin, reason=reason)

    def can_retrieve(self, document: Document) -> bool:
        return can_retrieve(document.metadata.lifecycle_state)

    def allowed_transitions(self, document: Document) -> list[State]:
        current = document.metadata.lifecycle_state
        return list(ALLOWED_TRANSITIONS.get(current, set()))

    def _emit_audit(
        self,
        document: Document,
        from_state: State,
        to_state: State,
        actor: str,
        reason: Optional[str],
    ) -> None:
        if not self.audit_logger:
            return
        try:
            self.audit_logger.log_lifecycle_transition(
                document_id=document.id,
                from_state=from_state.value,
                to_state=to_state.value,
                actor=actor,
                reason=reason,
            )
        except Exception as exc:
            logger.warning("Audit log emission failed: %s", exc)

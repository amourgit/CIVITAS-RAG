"""
civitas.core.exceptions.knowledge_exceptions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Domain exception hierarchy for the CIVITAS knowledge platform.

All exceptions derive from CivitasError, allowing callers
to catch the entire domain at once if needed.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID


# ─────────────────────────────────────────────────────────────
#  BASE
# ─────────────────────────────────────────────────────────────

class CivitasError(Exception):
    """Base exception for all CIVITAS domain errors."""

    def __init__(self, message: str, context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context or {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.message!r})"


# ─────────────────────────────────────────────────────────────
#  DOCUMENT ERRORS
# ─────────────────────────────────────────────────────────────

class DocumentError(CivitasError):
    """Base class for document-related errors."""


class DocumentNotFoundError(DocumentError):
    def __init__(self, document_id: UUID | str) -> None:
        super().__init__(f"Document not found: {document_id}", {"document_id": str(document_id)})
        self.document_id = document_id


class DocumentAlreadyExistsError(DocumentError):
    def __init__(self, document_id: UUID | str) -> None:
        super().__init__(f"Document already exists: {document_id}", {"document_id": str(document_id)})


class DocumentVersionConflictError(DocumentError):
    def __init__(self, document_id: UUID | str, expected: int, actual: int) -> None:
        super().__init__(
            f"Version conflict for document {document_id}: expected v{expected}, got v{actual}",
            {"document_id": str(document_id), "expected": expected, "actual": actual},
        )


class DocumentParsingError(DocumentError):
    def __init__(self, filename: str, reason: str) -> None:
        super().__init__(f"Failed to parse '{filename}': {reason}",
                         {"filename": filename, "reason": reason})


class DocumentValidationError(DocumentError):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"Document validation failed on field '{field}': {reason}",
                         {"field": field, "reason": reason})


# ─────────────────────────────────────────────────────────────
#  KNOWLEDGE SPACE ERRORS
# ─────────────────────────────────────────────────────────────

class KnowledgeSpaceError(CivitasError):
    """Base class for knowledge space errors."""


class KnowledgeSpaceNotFoundError(KnowledgeSpaceError):
    def __init__(self, space_id_or_name: str) -> None:
        super().__init__(f"Knowledge space not found: {space_id_or_name}",
                         {"space": space_id_or_name})


class KnowledgeSpaceNotActiveError(KnowledgeSpaceError):
    def __init__(self, space_name: str) -> None:
        super().__init__(f"Knowledge space '{space_name}' is not active",
                         {"space": space_name})


class KnowledgeSpaceCapacityError(KnowledgeSpaceError):
    def __init__(self, space_name: str, limit: int) -> None:
        super().__init__(
            f"Knowledge space '{space_name}' has reached its document limit of {limit}",
            {"space": space_name, "limit": limit},
        )


# ─────────────────────────────────────────────────────────────
#  TAXONOMY ERRORS
# ─────────────────────────────────────────────────────────────

class TaxonomyError(CivitasError):
    """Base class for taxonomy errors."""


class TaxonomyNodeNotFoundError(TaxonomyError):
    def __init__(self, path: str) -> None:
        super().__init__(f"Taxonomy node not found: '{path}'", {"path": path})


class InvalidTaxonomyPathError(TaxonomyError):
    def __init__(self, path: str, reason: str = "") -> None:
        super().__init__(
            f"Invalid taxonomy path '{path}'" + (f": {reason}" if reason else ""),
            {"path": path},
        )


class TaxonomyLeafRequiredError(TaxonomyError):
    def __init__(self, path: str) -> None:
        super().__init__(
            f"Documents must be classified to a leaf node; '{path}' is not a leaf",
            {"path": path},
        )


# ─────────────────────────────────────────────────────────────
#  INDEX ERRORS
# ─────────────────────────────────────────────────────────────

class IndexError(CivitasError):
    """Base class for index errors."""


class IndexNotFoundError(IndexError):
    def __init__(self, index_id: str) -> None:
        super().__init__(f"Index not found: {index_id}", {"index_id": index_id})
        self.index_id = index_id


class IndexNotBuiltError(IndexError):
    def __init__(self, index_id: str) -> None:
        super().__init__(
            f"Index '{index_id}' has not been built yet. Call build() first.",
            {"index_id": index_id},
        )


class IndexBuildError(IndexError):
    def __init__(self, index_id: str, reason: str) -> None:
        super().__init__(f"Failed to build index '{index_id}': {reason}",
                         {"index_id": index_id, "reason": reason})


class IndexUpdateError(IndexError):
    def __init__(self, index_id: str, reason: str) -> None:
        super().__init__(f"Failed to update index '{index_id}': {reason}",
                         {"index_id": index_id, "reason": reason})


# ─────────────────────────────────────────────────────────────
#  RETRIEVAL ERRORS
# ─────────────────────────────────────────────────────────────

class RetrievalError(CivitasError):
    """Base class for retrieval errors."""


class RetrievalStrategyNotFoundError(RetrievalError):
    def __init__(self, strategy: str) -> None:
        super().__init__(f"Retrieval strategy not found: {strategy}", {"strategy": strategy})


class EmptyQueryError(RetrievalError):
    def __init__(self) -> None:
        super().__init__("Query cannot be empty")


class RetrievalTimeoutError(RetrievalError):
    def __init__(self, timeout_ms: int) -> None:
        super().__init__(f"Retrieval timed out after {timeout_ms}ms", {"timeout_ms": timeout_ms})


# ─────────────────────────────────────────────────────────────
#  GOVERNANCE ERRORS
# ─────────────────────────────────────────────────────────────

class GovernanceError(CivitasError):
    """Base class for governance errors."""


class LifecycleTransitionError(GovernanceError):
    def __init__(self, document_id: UUID | str, from_state: str, to_state: str) -> None:
        super().__init__(
            f"Invalid lifecycle transition for document {document_id}: {from_state} → {to_state}",
            {"document_id": str(document_id), "from_state": from_state, "to_state": to_state},
        )


class AccessDeniedError(GovernanceError):
    def __init__(self, principal: str, resource: str, action: str) -> None:
        super().__init__(
            f"Access denied: '{principal}' cannot '{action}' on '{resource}'",
            {"principal": principal, "resource": resource, "action": action},
        )


class QualityGateRejectionError(GovernanceError):
    def __init__(self, document_id: UUID | str, score: float, threshold: float) -> None:
        super().__init__(
            f"Document {document_id} rejected by quality gate: score={score:.2f} < threshold={threshold:.2f}",
            {"document_id": str(document_id), "score": score, "threshold": threshold},
        )


# ─────────────────────────────────────────────────────────────
#  STORAGE ERRORS
# ─────────────────────────────────────────────────────────────

class StorageError(CivitasError):
    """Base class for storage errors."""


class VectorStoreError(StorageError):
    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(f"Vector store error during '{operation}': {reason}",
                         {"operation": operation, "reason": reason})


class DocumentStoreError(StorageError):
    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(f"Document store error during '{operation}': {reason}",
                         {"operation": operation, "reason": reason})


# ─────────────────────────────────────────────────────────────
#  INGESTION ERRORS
# ─────────────────────────────────────────────────────────────

class IngestionError(CivitasError):
    """Base class for ingestion pipeline errors."""


class ConnectorError(IngestionError):
    def __init__(self, connector: str, reason: str) -> None:
        super().__init__(f"Connector '{connector}' error: {reason}",
                         {"connector": connector, "reason": reason})


class UnsupportedFormatError(IngestionError):
    def __init__(self, format_name: str) -> None:
        super().__init__(f"Unsupported document format: '{format_name}'",
                         {"format": format_name})


class ChunkingError(IngestionError):
    def __init__(self, document_id: UUID | str, reason: str) -> None:
        super().__init__(f"Chunking failed for document {document_id}: {reason}",
                         {"document_id": str(document_id), "reason": reason})

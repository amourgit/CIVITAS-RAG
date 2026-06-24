"""
civitas.ingestion.pipeline
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ingestion Pipeline — orchestrates the full document lifecycle
from raw file to indexed knowledge.

Pipeline stages:
  1. FETCH      — connector fetches RawDocumentBlob
  2. PARSE      — parser extracts plain text
  3. ENRICH     — enricher adds derived metadata
  4. VALIDATE   — quality gate + schema validation
  5. CHUNK      — chunker splits into DocumentChunks
  6. INDEX      — index manager embeds and stores chunks
  7. GOVERN     — lifecycle manager updates document state

The pipeline is designed for monorepo use:
  · All stages are Python function calls (no HTTP)
  · Configurable per KnowledgeSpace
  · Fully observable (events + logging)
  · Resumable on failure (idempotent re-run)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID

from civitas.core.exceptions.knowledge_exceptions import (
    DocumentParsingError,
    IngestionError,
    QualityGateRejectionError,
    UnsupportedFormatError,
)
from civitas.core.models.chunk import DocumentChunk
from civitas.core.models.document import Document, DocumentFormat
from civitas.core.models.metadata import (
    DocumentLifecycleState,
    DocumentMetadata,
    DocumentProcessingInfo,
    DocumentSource,
    SourceType,
)
from civitas.ingestion.connectors.base import RawDocumentBlob
from civitas.ingestion.parsers.text import ParserRegistry
from civitas.ingestion.transformers.chunker import BaseChunker, SentenceChunker
from civitas.ingestion.transformers.enricher import DocumentEnricher

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  PIPELINE RESULT
# ─────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    """Result of processing one document through the pipeline."""
    document_id: Optional[UUID] = None
    success: bool = False
    stage_failed: Optional[str] = None
    error: Optional[str] = None
    chunks_produced: int = 0
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        return self.success

    @property
    def is_failure(self) -> bool:
        return not self.success


@dataclass
class PipelineStats:
    """Aggregate statistics for a pipeline run."""
    total_processed: int = 0
    total_succeeded: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    total_chunks: int = 0
    total_duration_ms: int = 0
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    errors: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_processed == 0:
            return 0.0
        return self.total_succeeded / self.total_processed


# ─────────────────────────────────────────────────────────────
#  PIPELINE CONFIG
# ─────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    """Configuration for one pipeline run."""
    knowledge_space_id: UUID
    knowledge_space_name: str
    domain: str
    category: str
    taxonomy_path: list[str] = field(default_factory=list)
    owner_id: str = "system"
    team_id: Optional[str] = None
    chunk_size: int = 512
    chunk_overlap: int = 64
    chunk_strategy: str = "sentence"         # sentence | semantic | hierarchical
    min_quality_score: float = 0.5
    auto_approve: bool = False               # Skip review if quality >= threshold
    auto_approve_threshold: float = 0.9
    embedding_model: str = "text-embedding-3-small"
    allowed_teams: list[str] = field(default_factory=list)
    allowed_roles: list[str] = field(default_factory=list)
    dry_run: bool = False                    # Parse & chunk but don't index


# ─────────────────────────────────────────────────────────────
#  PIPELINE
# ─────────────────────────────────────────────────────────────

_FORMAT_MAP: dict[str, DocumentFormat] = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".doc": DocumentFormat.DOCX,
    ".txt": DocumentFormat.TEXT,
    ".text": DocumentFormat.TEXT,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".html": DocumentFormat.HTML,
    ".htm": DocumentFormat.HTML,
    ".json": DocumentFormat.JSON,
    ".csv": DocumentFormat.CSV,
    ".xlsx": DocumentFormat.XLSX,
    ".xml": DocumentFormat.XML,
}


class IngestionPipeline:
    """
    Orchestrates the complete document ingestion pipeline.

    Injected dependencies:
      · parser_registry  — resolves parser by file extension
      · enricher         — adds derived metadata
      · chunker          — splits document into chunks
      · index_manager    — stores chunks in vector/keyword indexes (injected later)
      · doc_store        — persists Document entities (injected later)
      · audit_logger     — records audit events (injected later)

    Dependencies are optional at construction time to allow
    staged initialization and testing without storage.
    """

    def __init__(
        self,
        parser_registry: Optional[ParserRegistry] = None,
        enricher: Optional[DocumentEnricher] = None,
        chunker: Optional[BaseChunker] = None,
        index_manager: Optional[object] = None,   # civitas.indexing.registry.IndexRegistry
        doc_store: Optional[object] = None,        # civitas.storage.document.postgres.PostgresDocumentStore
        audit_logger: Optional[object] = None,     # civitas.governance.audit.logger.AuditLogger
    ) -> None:
        self.parser_registry = parser_registry or ParserRegistry()
        self.enricher = enricher or DocumentEnricher()
        self.chunker = chunker or SentenceChunker()
        self.index_manager = index_manager
        self.doc_store = doc_store
        self.audit_logger = audit_logger

    # ── Main Entry Point ───────────────────────────────────────

    def process_blob(
        self,
        blob: RawDocumentBlob,
        config: PipelineConfig,
        title: Optional[str] = None,
    ) -> PipelineResult:
        """
        Process a single RawDocumentBlob through all pipeline stages.
        Returns a PipelineResult with success/failure information.
        """
        start_ms = int(time.monotonic() * 1000)
        result = PipelineResult()

        try:
            # Stage 1: Parse
            document = self._stage_parse(blob, config, title)
            result.document_id = document.id

            # Stage 2: Enrich
            document = self._stage_enrich(document)

            # Stage 3: Validate quality gate
            self._stage_validate(document, config)

            # Stage 4: Chunk
            chunks = self._stage_chunk(document, config)
            result.chunks_produced = len(chunks)

            # Stage 5: Index (if not dry run and index_manager available)
            if not config.dry_run and self.index_manager:
                self._stage_index(document, chunks, config)

            # Stage 6: Persist document
            if not config.dry_run and self.doc_store:
                self._stage_persist(document)

            # Stage 7: Govern (lifecycle transition)
            self._stage_govern(document, config)

            result.success = True
            logger.info(
                "✓ Ingested '%s' → %d chunks [%s]",
                blob.filename, len(chunks), document.id,
            )

        except QualityGateRejectionError as exc:
            result.stage_failed = "validate"
            result.error = str(exc)
            result.warnings.append("Document rejected by quality gate — review manually")
            logger.warning("Quality gate rejection: %s", exc)

        except UnsupportedFormatError as exc:
            result.stage_failed = "parse"
            result.error = str(exc)
            logger.warning("Unsupported format: %s", exc)

        except Exception as exc:
            result.stage_failed = result.stage_failed or "unknown"
            result.error = f"{type(exc).__name__}: {exc}"
            logger.exception("Pipeline error processing '%s'", blob.filename)

        finally:
            result.duration_ms = int(time.monotonic() * 1000) - start_ms

        return result

    # ── Stages ────────────────────────────────────────────────

    def _stage_parse(
        self,
        blob: RawDocumentBlob,
        config: PipelineConfig,
        title: Optional[str],
    ) -> Document:
        """Stage 1: Parse raw bytes into a Document with text content."""
        parser = self.parser_registry.get(blob.file_extension)
        if not parser:
            raise UnsupportedFormatError(blob.file_extension)

        parse_result = parser.safe_parse(blob)
        if not parse_result.is_success:
            raise DocumentParsingError(blob.filename, parse_result.error or "Unknown parsing error")

        from civitas.core.models.metadata import (
            DocumentOwnership,
            DocumentAccessControl,
            AccessLevel,
        )

        doc_title = title or blob.filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
        metadata = DocumentMetadata(
            domain=config.domain,
            category=config.category,
            taxonomy_path=config.taxonomy_path,
            knowledge_space_id=config.knowledge_space_id,
            knowledge_space_name=config.knowledge_space_name,
            ownership=DocumentOwnership(
                owner_id=config.owner_id,
                team_id=config.team_id,
            ),
            source=DocumentSource(
                source_type=blob.source_type,
                source_path=blob.source_path,
                source_url=blob.source_url,
                original_filename=blob.filename,
            ),
            access_control=DocumentAccessControl(
                access_level=AccessLevel.TEAM if config.allowed_teams else AccessLevel.SPACE,
                allowed_teams=config.allowed_teams,
                allowed_roles=config.allowed_roles,
            ),
            processing=DocumentProcessingInfo(
                ingestion_pipeline="default-v1",
                chunk_strategy=config.chunk_strategy,
                embedding_model=config.embedding_model,
            ),
        )

        document = Document(
            title=doc_title,
            format=_FORMAT_MAP.get(blob.file_extension, DocumentFormat.UNKNOWN),
            byte_size=blob.byte_size,
            page_count=parse_result.page_count,
            metadata=metadata,
        )
        document.set_content(parse_result.text)
        return document

    def _stage_enrich(self, document: Document) -> Document:
        """Stage 2: Add derived metadata (language, keywords, quality)."""
        return self.enricher.enrich(document)

    def _stage_validate(self, document: Document, config: PipelineConfig) -> None:
        """Stage 3: Quality gate — reject documents below threshold."""
        quality = document.metadata.quality
        score = quality.composite_score
        if score is not None and score < config.min_quality_score:
            raise QualityGateRejectionError(document.id, score, config.min_quality_score)

    def _stage_chunk(self, document: Document, config: PipelineConfig) -> list[DocumentChunk]:
        """Stage 4: Split document into DocumentChunks."""
        chunks = self.chunker.chunk(document)
        document.chunk_ids = [c.id for c in chunks]
        document.metadata.processing.chunk_count = len(chunks)
        return chunks

    def _stage_index(
        self,
        document: Document,
        chunks: list[DocumentChunk],
        config: PipelineConfig,
    ) -> None:
        """Stage 5: Add chunks to all configured indexes for the knowledge space."""
        if self.index_manager:
            self.index_manager.add_chunks(
                chunks=chunks,
                knowledge_space_id=str(config.knowledge_space_id),
            )
            document.mark_indexed()

    def _stage_persist(self, document: Document) -> None:
        """Stage 6: Persist document entity to the document store."""
        if self.doc_store:
            self.doc_store.save(document)

    def _stage_govern(self, document: Document, config: PipelineConfig) -> None:
        """Stage 7: Update document lifecycle state post-ingestion."""
        quality_score = document.metadata.quality.composite_score or 0.0
        if config.auto_approve and quality_score >= config.auto_approve_threshold:
            document.metadata.lifecycle_state = DocumentLifecycleState.ACTIVE
            document.metadata.approved_at = datetime.utcnow()
            document.metadata.approved_by = "system:auto-approve"
        else:
            document.metadata.lifecycle_state = DocumentLifecycleState.PENDING_REVIEW
        document.metadata.processing.processed_at = datetime.utcnow()
        document.touch()

    # ── Batch Processing ───────────────────────────────────────

    def process_many(
        self,
        blobs: list[RawDocumentBlob],
        config: PipelineConfig,
    ) -> PipelineStats:
        """Process a list of blobs and return aggregate statistics."""
        stats = PipelineStats()
        for blob in blobs:
            result = self.process_blob(blob, config)
            stats.total_processed += 1
            stats.total_duration_ms += result.duration_ms
            if result.is_success:
                stats.total_succeeded += 1
                stats.total_chunks += result.chunks_produced
            else:
                stats.total_failed += 1
                if result.error:
                    stats.errors.append(f"{blob.filename}: {result.error}")
        stats.finished_at = datetime.utcnow()
        logger.info(
            "Pipeline run complete: %d/%d succeeded, %d chunks, %.1f%% success rate",
            stats.total_succeeded, stats.total_processed,
            stats.total_chunks, stats.success_rate * 100,
        )
        return stats

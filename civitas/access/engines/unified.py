"""
civitas.access.engines.unified
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UnifiedKnowledgeEngine — the single entry point for all knowledge access.

This is the ONLY component that future LangGraph agents interact with.
It abstracts:
  · Retrieval strategy selection (based on intent + space config)
  · GraphRAG context augmentation
  · Access control enforcement
  · Result formatting for LLM consumption
  · Audit trail emission

Architecture:
  LangGraph Agent Tool
    → UnifiedKnowledgeEngine.query(KnowledgeQuery)
      → RetrievalEngine (multi-strategy retrieval)
        → GraphRAGQueryEngine (optional graph context)
          → AccessControl (RBAC enforcement)
            → AuditLogger (audit trail)
              → KnowledgeResponse (typed, LLM-ready)

This component is intentionally the thinnest possible layer
over the retrieval engine — its job is orchestration, not logic.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from civitas.access.contracts.query import (
    KnowledgeQuery,
    KnowledgeResponse,
    KnowledgeResult,
    QueryIntent,
)
from civitas.core.models.knowledge_space import IndexStrategy
from civitas.retrieval.engine import RetrievalEngine, RetrievalQuery, RetrievalResult

logger = logging.getLogger(__name__)


# Mapping from QueryIntent → preferred IndexStrategy
INTENT_TO_STRATEGY: dict[QueryIntent, IndexStrategy] = {
    QueryIntent.FACTUAL:       IndexStrategy.HYBRID,
    QueryIntent.SUMMARY:       IndexStrategy.HIERARCHICAL,
    QueryIntent.COMPARISON:    IndexStrategy.HYBRID,
    QueryIntent.EXPLORATION:   IndexStrategy.GRAPH_ENHANCED,
    QueryIntent.VERIFICATION:  IndexStrategy.FULL,
    QueryIntent.PROCEDURAL:    IndexStrategy.KEYWORD,
    QueryIntent.REGULATORY:    IndexStrategy.HYBRID,
}


class UnifiedKnowledgeEngine:
    """
    The Knowledge Access Layer entry point.

    This is the public API of the CIVITAS knowledge platform.
    Future LangGraph agent tools call this — nothing else.

    Design principles:
      · One method to call: query()
      · Returns KnowledgeResponse — always typed, always LLM-ready
      · Never raises retrieval errors to callers (returns empty with warnings)
      · Always enforces access control
      · Always emits audit events

    Usage (future LangGraph tool):
        engine = UnifiedKnowledgeEngine(...)

        result = engine.query(KnowledgeQuery(
            query="What are the notice periods in our service agreements?",
            intent=QueryIntent.REGULATORY,
            knowledge_space="legal-contracts",
            agent_id="legal-research-agent",
        ))

        context = result.to_context_string()
        # → Feed context into LLM prompt
    """

    def __init__(
        self,
        retrieval_engine: RetrievalEngine,
        graphrag_engines: Optional[dict[str, object]] = None,   # space_name → GraphRAGQueryEngine
        audit_logger: Optional[object] = None,
        space_registry: Optional[object] = None,
    ) -> None:
        self.retrieval_engine = retrieval_engine
        self.graphrag_engines = graphrag_engines or {}
        self.audit_logger = audit_logger
        self.space_registry = space_registry

    def query(self, knowledge_query: KnowledgeQuery) -> KnowledgeResponse:
        """
        Execute a knowledge query and return a typed, LLM-ready response.

        This is the only public method. Never call the retrieval engine directly.
        """
        t0 = int(time.monotonic() * 1000)
        warnings: list[str] = []

        # ── Build retrieval query ──────────────────────────────
        strategy = INTENT_TO_STRATEGY.get(knowledge_query.intent, IndexStrategy.HYBRID)
        filters = self._build_filters(knowledge_query)

        retrieval_query = RetrievalQuery(
            query_text=knowledge_query.query,
            knowledge_space_name=knowledge_query.knowledge_space,
            top_k=knowledge_query.top_k,
            min_score=knowledge_query.min_score,
            strategy=strategy,
            filters=filters if filters else None,
            requesting_agent_id=knowledge_query.agent_id,
            requesting_team_id=knowledge_query.requesting_team,
            requesting_role=knowledge_query.requesting_role,
            include_metadata=knowledge_query.include_metadata,
            rerank=True,
        )

        # ── Execute retrieval ──────────────────────────────────
        try:
            retrieval_response = self.retrieval_engine.retrieve(retrieval_query)
        except Exception as exc:
            logger.error("Retrieval engine error: %s", exc)
            warnings.append(f"Retrieval error: {exc}")
            retrieval_response = None

        # ── Convert to KnowledgeResult ─────────────────────────
        knowledge_results: list[KnowledgeResult] = []
        if retrieval_response:
            for rr in retrieval_response.results:
                content = rr.content
                if not knowledge_query.include_full_content:
                    content = content[:knowledge_query.max_content_chars]
                knowledge_results.append(KnowledgeResult(
                    chunk_id=rr.chunk_id,
                    document_id=rr.document_id,
                    document_title=rr.document_title,
                    content=content,
                    score=rr.score,
                    domain=rr.domain,
                    category=rr.category,
                    knowledge_space=rr.knowledge_space_name,
                    taxonomy_path=rr.taxonomy_path,
                    page_number=rr.page_number,
                    section=rr.section_heading,
                    tags=rr.tags,
                ))

        # ── GraphRAG context (optional) ────────────────────────
        graph_context: Optional[str] = None
        if knowledge_query.include_graph_context and knowledge_query.knowledge_space:
            graph_context = self._get_graph_context(
                query=knowledge_query.query,
                space_name=knowledge_query.knowledge_space,
            )

        # ── Record space query ─────────────────────────────────
        if self.space_registry and knowledge_query.knowledge_space:
            try:
                self.space_registry.record_query(knowledge_query.knowledge_space)
            except Exception:
                pass

        # ── Emit audit event ───────────────────────────────────
        latency_ms = int(time.monotonic() * 1000) - t0
        self._emit_audit(knowledge_query, len(knowledge_results), latency_ms)

        return KnowledgeResponse(
            query=knowledge_query.query,
            intent=knowledge_query.intent,
            results=knowledge_results,
            total_found=len(knowledge_results),
            knowledge_space=knowledge_query.knowledge_space,
            graph_context=graph_context,
            latency_ms=latency_ms,
            warnings=warnings,
        )

    # ── Multi-space federated query ────────────────────────────

    def query_multiple_spaces(
        self,
        knowledge_query: KnowledgeQuery,
        space_names: list[str],
    ) -> KnowledgeResponse:
        """
        Query multiple knowledge spaces and merge results.
        Used when an agent has access to several spaces and wants broad coverage.
        """
        all_results: list[KnowledgeResult] = []
        warnings: list[str] = []

        for space_name in space_names:
            sub_query = KnowledgeQuery(
                **{**knowledge_query.__dict__, "knowledge_space": space_name}
            )
            response = self.query(sub_query)
            all_results.extend(response.results)
            warnings.extend(response.warnings)

        # Re-rank merged results by score
        all_results.sort(key=lambda r: r.score, reverse=True)
        top_results = all_results[:knowledge_query.top_k]

        return KnowledgeResponse(
            query=knowledge_query.query,
            intent=knowledge_query.intent,
            results=top_results,
            total_found=len(top_results),
            knowledge_space=None,   # Multi-space
            warnings=warnings,
        )

    # ── Internal Helpers ───────────────────────────────────────

    def _build_filters(self, query: KnowledgeQuery) -> dict:
        filters: dict = {}
        if query.domains:
            filters["domain"] = query.domains[0]   # pgvector supports single-value filters
        return filters

    def _get_graph_context(self, query: str, space_name: str) -> Optional[str]:
        from civitas.graphrag.query import GraphQueryMode
        engine = self.graphrag_engines.get(space_name)
        if not engine:
            return None
        try:
            result = engine.query(query, mode=GraphQueryMode.HYBRID)
            return result.context or None
        except Exception as exc:
            logger.warning("GraphRAG context retrieval failed: %s", exc)
            return None

    def _emit_audit(
        self,
        query: KnowledgeQuery,
        result_count: int,
        latency_ms: int,
    ) -> None:
        if not self.audit_logger:
            return
        try:
            self.audit_logger.log_retrieval(
                query=query.query,
                knowledge_space_name=query.knowledge_space or "global",
                results_count=result_count,
                latency_ms=latency_ms,
                strategies=[query.intent.value],
                requesting_principal=query.agent_id,
            )
        except Exception as exc:
            logger.warning("Audit emission failed: %s", exc)

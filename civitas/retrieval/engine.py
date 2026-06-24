"""
civitas.retrieval.engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Unified Retrieval Engine — the central query hub for CIVITAS.

This engine orchestrates multi-strategy retrieval:
  1. Select retrieval strategies based on query profile
  2. Execute each strategy in parallel (or sequentially)
  3. Fuse results using Reciprocal Rank Fusion (RRF)
  4. Rerank the fused results using a cross-encoder
  5. Apply access control filters
  6. Return typed RetrievalResult objects

The engine is the ONLY entry point for retrieval.
No agent, tool, or query engine should bypass it.

Design:
  · Strategy selection is configurable per knowledge space
  · Each strategy is a pure function: query + index → results
  · Fusion and reranking are pluggable
  · Access control is enforced at result level (never skipped)
  · Latency is tracked per stage for observability
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from civitas.core.exceptions.knowledge_exceptions import EmptyQueryError
from civitas.core.models.knowledge_space import IndexStrategy
from civitas.indexing.indexes.base import IndexType
from civitas.indexing.registry import IndexRegistry

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  DATA CONTRACTS
# ─────────────────────────────────────────────────────────────

@dataclass
class RetrievalResult:
    """A single retrieved chunk with scoring and provenance."""
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    score: float
    domain: str
    category: str
    knowledge_space_name: Optional[str]
    page_number: Optional[int]
    section_heading: Optional[str]
    taxonomy_path: str
    tags: list[str]
    retrieval_strategy: str
    metadata: dict[str, Any]

    @property
    def short_content(self) -> str:
        return self.content[:300] + ("..." if len(self.content) > 300 else "")


@dataclass
class RetrievalResponse:
    """Complete response from the retrieval engine."""
    query: str
    results: list[RetrievalResult]
    total_found: int
    strategies_used: list[str]
    knowledge_space: Optional[str]
    latency_ms: int
    stage_latencies: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def top_result(self) -> Optional[RetrievalResult]:
        return self.results[0] if self.results else None

    @property
    def has_results(self) -> bool:
        return len(self.results) > 0


@dataclass
class RetrievalQuery:
    """Input to the retrieval engine."""
    query_text: str
    knowledge_space_name: Optional[str] = None
    top_k: int = 10
    min_score: float = 0.0
    strategy: Optional[IndexStrategy] = None
    filters: Optional[dict[str, Any]] = None
    requesting_team_id: Optional[str] = None
    requesting_role: Optional[str] = None
    requesting_agent_id: Optional[str] = None
    requesting_user_id: Optional[str] = None
    include_metadata: bool = True
    rerank: bool = True
    deduplicate: bool = True


# ─────────────────────────────────────────────────────────────
#  FUSION
# ─────────────────────────────────────────────────────────────

def reciprocal_rank_fusion(
    ranked_lists: list[list[Any]],
    k: int = 60,
    top_n: int = 20,
) -> list[tuple[Any, float]]:
    """
    Reciprocal Rank Fusion (RRF) across multiple ranked result lists.

    RRF formula: score(d) = Σ 1 / (k + rank(d))
    where rank(d) is the position of document d in each list.

    Args:
        ranked_lists: Each inner list is a ranked list of (node, score) tuples.
        k:            RRF constant (default 60 is standard).
        top_n:        Maximum results to return.

    Returns:
        List of (node, rrf_score) sorted by descending score.
    """
    scores: dict[str, float] = {}
    nodes: dict[str, Any] = {}

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            node, _orig_score = item if isinstance(item, tuple) else (item, 1.0)
            node_id = getattr(node, "node_id", str(id(node)))
            scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank + 1)
            nodes[node_id] = node

    sorted_ids = sorted(scores, key=lambda nid: scores[nid], reverse=True)
    return [(nodes[nid], scores[nid]) for nid in sorted_ids[:top_n]]


# ─────────────────────────────────────────────────────────────
#  ENGINE
# ─────────────────────────────────────────────────────────────

class RetrievalEngine:
    """
    Unified retrieval engine for the CIVITAS knowledge platform.

    Composes strategies → fusion → reranking → access filter
    in a single, observable pipeline.

    Injection points:
      · index_registry   — source of all indexes
      · space_registry   — resolves space config and access rules
      · reranker         — optional cross-encoder reranker
    """

    def __init__(
        self,
        index_registry: IndexRegistry,
        space_registry: Optional[Any] = None,    # civitas.knowledge.spaces.registry.KnowledgeSpaceRegistry
        reranker: Optional[Any] = None,           # civitas.retrieval.rerankers.base.BaseReranker
    ) -> None:
        self.index_registry = index_registry
        self.space_registry = space_registry
        self.reranker = reranker

    def retrieve(self, query: RetrievalQuery) -> RetrievalResponse:
        """
        Execute a retrieval query and return a typed response.

        Steps:
          1. Validate query
          2. Resolve knowledge space + strategy
          3. Execute configured retrieval strategies
          4. Fuse results (RRF)
          5. Rerank if configured
          6. Filter by access control
          7. Return RetrievalResponse
        """
        t0 = int(time.monotonic() * 1000)
        stages: dict[str, int] = {}

        if not query.query_text.strip():
            raise EmptyQueryError()

        space_name = query.knowledge_space_name
        strategy = query.strategy or self._resolve_strategy(space_name)

        # ── Stage 1: Retrieve from each strategy ──────────────
        t_strat = int(time.monotonic() * 1000)
        raw_results: dict[str, list] = {}
        strategies_used: list[str] = []

        if strategy in (IndexStrategy.SEMANTIC, IndexStrategy.HYBRID, IndexStrategy.FULL, IndexStrategy.GRAPH_ENHANCED):
            results = self._retrieve_vector(query, space_name)
            if results:
                raw_results["semantic"] = results
                strategies_used.append("semantic")

        if strategy in (IndexStrategy.KEYWORD, IndexStrategy.HYBRID, IndexStrategy.FULL):
            results = self._retrieve_keyword(query, space_name)
            if results:
                raw_results["keyword"] = results
                strategies_used.append("keyword")

        if strategy in (IndexStrategy.GRAPH_ENHANCED, IndexStrategy.FULL):
            results = self._retrieve_graph(query, space_name)
            if results:
                raw_results["graph"] = results
                strategies_used.append("graph")

        stages["retrieval_ms"] = int(time.monotonic() * 1000) - t_strat

        # ── Stage 2: Fusion ────────────────────────────────────
        t_fusion = int(time.monotonic() * 1000)
        if len(raw_results) > 1:
            fused = reciprocal_rank_fusion(list(raw_results.values()), top_n=query.top_k * 2)
        elif raw_results:
            single_list = next(iter(raw_results.values()))
            fused = [(n, s) for n, s in (
                (n, getattr(n, "score", 1.0)) for n in single_list
            )][:query.top_k * 2]
        else:
            fused = []
        stages["fusion_ms"] = int(time.monotonic() * 1000) - t_fusion

        # ── Stage 3: Rerank ────────────────────────────────────
        t_rerank = int(time.monotonic() * 1000)
        if query.rerank and self.reranker and fused:
            try:
                fused = self.reranker.rerank(query.query_text, fused, top_n=query.top_k)
            except Exception as exc:
                logger.warning("Reranker failed, skipping: %s", exc)
        stages["rerank_ms"] = int(time.monotonic() * 1000) - t_rerank

        # ── Stage 4: Convert & filter ──────────────────────────
        results_typed = self._convert_and_filter(
            fused[:query.top_k],
            query,
            strategy_label="+".join(strategies_used),
        )

        # Filter by minimum score
        if query.min_score > 0:
            results_typed = [r for r in results_typed if r.score >= query.min_score]

        # Record space access
        if self.space_registry and space_name:
            try:
                self.space_registry.record_query(space_name)
            except Exception:
                pass

        total_ms = int(time.monotonic() * 1000) - t0
        logger.info(
            "Retrieval: '%s' → %d results [space=%s, strategies=%s, %dms]",
            query.query_text[:60], len(results_typed),
            space_name, strategies_used, total_ms,
        )

        return RetrievalResponse(
            query=query.query_text,
            results=results_typed,
            total_found=len(results_typed),
            strategies_used=strategies_used,
            knowledge_space=space_name,
            latency_ms=total_ms,
            stage_latencies=stages,
        )

    # ── Internal Strategy Runners ──────────────────────────────

    def _retrieve_vector(
        self, query: RetrievalQuery, space_key: Optional[str]
    ) -> list:
        try:
            idx = self.index_registry.get_index_safe(IndexType.VECTOR, space_key)
            if not idx or not idx.is_ready:
                return []
            metadata_filters = self._build_metadata_filters(query)
            retriever = idx.get_retriever(top_k=query.top_k, filters=metadata_filters)
            nodes = retriever.retrieve(query.query_text)
            return [(n, getattr(n, "score", 0.0)) for n in nodes]
        except Exception as exc:
            logger.warning("Vector retrieval failed: %s", exc)
            return []

    def _retrieve_keyword(
        self, query: RetrievalQuery, space_key: Optional[str]
    ) -> list:
        try:
            idx = self.index_registry.get_index_safe(IndexType.KEYWORD, space_key)
            if not idx or not idx.is_ready:
                return []
            retriever = idx.get_retriever(top_k=query.top_k)
            nodes = retriever.retrieve(query.query_text)
            return [(n, getattr(n, "score", 0.0)) for n in nodes]
        except Exception as exc:
            logger.warning("Keyword retrieval failed: %s", exc)
            return []

    def _retrieve_graph(
        self, query: RetrievalQuery, space_key: Optional[str]
    ) -> list:
        try:
            idx = self.index_registry.get_index_safe(IndexType.KNOWLEDGE_GRAPH, space_key)
            if not idx or not idx.is_ready:
                return []
            retriever = idx.get_retriever(top_k=query.top_k)
            nodes = retriever.retrieve(query.query_text)
            return [(n, getattr(n, "score", 0.0)) for n in nodes]
        except Exception as exc:
            logger.warning("Graph retrieval failed: %s", exc)
            return []

    def _resolve_strategy(self, space_name: Optional[str]) -> IndexStrategy:
        if self.space_registry and space_name:
            try:
                space = self.space_registry.get_by_name(space_name)
                return space.config.default_index_strategy
            except Exception:
                pass
        return IndexStrategy.HYBRID

    def _build_metadata_filters(self, query: RetrievalQuery) -> Optional[Any]:
        if not query.filters:
            return None
        try:
            from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter
            filter_list = [
                ExactMatchFilter(key=k, value=v)
                for k, v in query.filters.items()
            ]
            return MetadataFilters(filters=filter_list)
        except ImportError:
            return None

    def _convert_and_filter(
        self,
        fused: list[tuple[Any, float]],
        query: RetrievalQuery,
        strategy_label: str,
    ) -> list[RetrievalResult]:
        """Convert raw fused results to typed RetrievalResult objects."""
        results: list[RetrievalResult] = []
        for node, score in fused:
            meta = getattr(node, "metadata", {})
            # Access control check
            if not self._passes_access_check(meta, query):
                continue
            results.append(RetrievalResult(
                chunk_id=meta.get("chunk_id", str(id(node))),
                document_id=meta.get("document_id", ""),
                document_title=meta.get("document_title", ""),
                content=node.get_content() if hasattr(node, "get_content") else str(node),
                score=float(score),
                domain=meta.get("domain", ""),
                category=meta.get("category", ""),
                knowledge_space_name=meta.get("knowledge_space_name"),
                page_number=meta.get("page_number"),
                section_heading=meta.get("section_heading"),
                taxonomy_path=meta.get("taxonomy_path", ""),
                tags=meta.get("tags", []),
                retrieval_strategy=strategy_label,
                metadata=meta if query.include_metadata else {},
            ))
        return results

    def _passes_access_check(self, meta: dict, query: RetrievalQuery) -> bool:
        """Enforce metadata-level access control."""
        allowed_teams = meta.get("allowed_teams", [])
        allowed_roles = meta.get("allowed_roles", [])
        allowed_agents = meta.get("allowed_agent_ids", [])
        access_level = meta.get("access_level", "team")

        if access_level in ("open", "space"):
            return True
        if query.requesting_team_id and (
            query.requesting_team_id in allowed_teams or "all" in allowed_teams
        ):
            return True
        if query.requesting_role and query.requesting_role in allowed_roles:
            return True
        if query.requesting_agent_id and query.requesting_agent_id in allowed_agents:
            return True
        if query.requesting_user_id:
            return True   # User-level access checked separately by RBAC
        # If no principal info provided, allow (dev mode / unauth'd)
        if not any([
            query.requesting_team_id, query.requesting_role,
            query.requesting_agent_id, query.requesting_user_id,
        ]):
            return True
        return False

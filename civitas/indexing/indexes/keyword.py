"""
civitas.indexing.indexes.keyword
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KeywordKnowledgeIndex — BM25 full-text search index.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from civitas.indexing.indexes.base import BaseKnowledgeIndex, IndexConfig, IndexType

logger = logging.getLogger(__name__)


class KeywordKnowledgeIndex(BaseKnowledgeIndex):
    """
    BM25-based keyword search index.

    Uses LlamaIndex BM25Retriever backed by a BM25 corpus.
    Best for exact-match queries, proper nouns, reference numbers,
    and cases where semantic search underperforms.

    Complements the VectorKnowledgeIndex in hybrid retrieval.
    """

    def __init__(self, config: IndexConfig) -> None:
        super().__init__(config)
        self._nodes: list[Any] = []
        self._bm25: Optional[Any] = None

    def build(self, nodes: list[Any]) -> None:
        self._mark_building()
        try:
            self._nodes = nodes
            self._bm25 = self._build_bm25(nodes)
            self.config.node_count = len(nodes)
            self._mark_ready()
        except Exception as exc:
            self._mark_error(exc)

    def add_nodes(self, nodes: list[Any]) -> None:
        self._nodes.extend(nodes)
        self._bm25 = self._build_bm25(self._nodes)
        self.config.node_count = len(self._nodes)

    def delete_node(self, node_id: str) -> None:
        self._nodes = [n for n in self._nodes if n.node_id != node_id]
        self._bm25 = self._build_bm25(self._nodes)
        self.config.node_count = len(self._nodes)

    def get_retriever(self, top_k: int = 10, **kwargs: Any) -> Any:
        if self._bm25 is None:
            raise RuntimeError("BM25 index not built. Call build() first.")
        try:
            from llama_index.retrievers.bm25 import BM25Retriever
            return BM25Retriever.from_defaults(
                nodes=self._nodes,
                similarity_top_k=top_k,
            )
        except ImportError:
            logger.warning("llama-index-retrievers-bm25 not installed. Using simple keyword retriever.")
            return self._simple_keyword_retriever(top_k)

    def _build_bm25(self, nodes: list[Any]) -> object:
        try:
            from rank_bm25 import BM25Okapi
            corpus = [n.get_content().lower().split() for n in nodes]
            return BM25Okapi(corpus)
        except ImportError:
            logger.warning("rank_bm25 not installed — keyword retrieval degraded.")
            return None

    def _simple_keyword_retriever(self, top_k: int) -> Any:
        """Fallback keyword retriever — simple substring matching."""
        class SimpleKeywordRetriever:
            def __init__(self, nodes: list, k: int) -> None:
                self._nodes = nodes
                self._k = k

            def retrieve(self, query: str) -> list:
                from llama_index.core.schema import NodeWithScore
                query_words = set(query.lower().split())
                scored = []
                for node in self._nodes:
                    content = node.get_content().lower()
                    hits = sum(1 for w in query_words if w in content)
                    if hits > 0:
                        scored.append((hits, node))
                scored.sort(key=lambda x: x[0], reverse=True)
                return [NodeWithScore(node=n, score=s / len(query_words)) for s, n in scored[:self._k]]

        return SimpleKeywordRetriever(self._nodes, top_k)

    def persist(self) -> None:
        """BM25 is rebuilt from nodes each time — no persistence needed."""

    def load(self) -> None:
        """BM25 is rebuilt from nodes — no restoration needed."""

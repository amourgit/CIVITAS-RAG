"""
civitas.indexing.indexes.summary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SummaryKnowledgeIndex — document-level LLM-generated summaries.

Each document gets one summary node (not chunk-level).
Enables fast "what is this document about?" retrieval
without needing to scan all chunks.

Use case: overview queries, document routing, sparse retrieval first pass.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from civitas.indexing.indexes.base import BaseKnowledgeIndex, IndexConfig

logger = logging.getLogger(__name__)


class SummaryKnowledgeIndex(BaseKnowledgeIndex):
    """
    Document-level summary index.
    One summarized node per document → fast document routing.
    Used as a first-stage retriever in hierarchical retrieval.
    """

    def __init__(self, config: IndexConfig, llm: Optional[Any] = None) -> None:
        super().__init__(config)
        self.llm = llm
        self._index: Optional[Any] = None

    def build(self, nodes: list[Any]) -> None:
        self._mark_building()
        try:
            from llama_index.core import SummaryIndex
            self._index = SummaryIndex(nodes=nodes, llm=self.llm)
            self.config.node_count = len(nodes)
            self._mark_ready()
        except Exception as exc:
            self._mark_error(exc)

    def add_nodes(self, nodes: list[Any]) -> None:
        if self._index is None:
            self.build(nodes)
            return
        for node in nodes:
            self._index.insert_nodes([node])
        self.config.node_count += len(nodes)

    def delete_node(self, node_id: str) -> None:
        if self._index:
            self._index.delete_nodes([node_id])

    def get_retriever(self, top_k: int = 5, **kwargs: Any) -> Any:
        if self._index is None:
            raise RuntimeError("Summary index not built.")
        return self._index.as_retriever(similarity_top_k=top_k)


"""
civitas.indexing.indexes.graph
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GraphKnowledgeIndex — LlamaIndex KnowledgeGraphIndex.

Extracts entity-relation triplets from each node and stores them
in a graph store. Enables graph-traversal retrieval:
  "Find all documents related to Entity X."

Works in concert with the GraphRAG module for community detection.
"""

from civitas.indexing.indexes.base import IndexConfig


class GraphKnowledgeIndex(BaseKnowledgeIndex):
    """
    Knowledge graph index for entity-relation retrieval.

    Triplet extraction: (subject, predicate, object) from each chunk.
    Storage: PostgreSQL graph table (via custom graph store).
    Retrieval: keyword-based entity lookup → graph traversal.

    For full GraphRAG (community summaries + global queries),
    see civitas.graphrag.
    """

    def __init__(
        self,
        config: IndexConfig,
        llm: Optional[Any] = None,
        max_triplets_per_chunk: int = 10,
    ) -> None:
        super().__init__(config)
        self.llm = llm
        self.max_triplets_per_chunk = max_triplets_per_chunk
        self._index: Optional[Any] = None

    def build(self, nodes: list[Any]) -> None:
        self._mark_building()
        try:
            from llama_index.core import KnowledgeGraphIndex
            self._index = KnowledgeGraphIndex(
                nodes=nodes,
                llm=self.llm,
                max_triplets_per_chunk=self.max_triplets_per_chunk,
                show_progress=True,
                include_embeddings=True,
            )
            self.config.node_count = len(nodes)
            self._mark_ready()
        except Exception as exc:
            logger.error("GraphIndex build failed: %s", exc)
            self._mark_error(exc)

    def add_nodes(self, nodes: list[Any]) -> None:
        if self._index is None:
            self.build(nodes)
            return
        for node in nodes:
            self._index.insert_nodes([node])
        self.config.node_count += len(nodes)

    def delete_node(self, node_id: str) -> None:
        if self._index:
            self._index.delete_nodes([node_id])

    def get_retriever(
        self,
        top_k: int = 5,
        retriever_mode: str = "keyword",
        **kwargs: Any,
    ) -> Any:
        """
        Args:
            retriever_mode: 'keyword' | 'embedding' | 'hybrid'
        """
        if self._index is None:
            raise RuntimeError("Graph index not built.")
        return self._index.as_retriever(
            retriever_mode=retriever_mode,
            similarity_top_k=top_k,
        )

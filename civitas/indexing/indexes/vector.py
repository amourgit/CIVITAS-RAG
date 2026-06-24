"""
civitas.indexing.indexes.vector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VectorKnowledgeIndex — semantic search via pgvector.

Wraps LlamaIndex VectorStoreIndex backed by PGVectorStore.
Each knowledge space gets its own table in PostgreSQL.

Supports:
  · Dense vector similarity search (cosine / L2 / inner product)
  · Metadata filtering (domain, space, access control, tags)
  · Batch upsert for efficient bulk ingestion
  · Incremental node addition without full rebuild
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from civitas.core.exceptions.knowledge_exceptions import IndexBuildError, IndexNotBuiltError
from civitas.indexing.indexes.base import BaseKnowledgeIndex, IndexConfig, IndexStatus, IndexType

logger = logging.getLogger(__name__)


class VectorKnowledgeIndex(BaseKnowledgeIndex):
    """
    Semantic search index backed by pgvector + LlamaIndex.

    Architecture:
      DocumentChunk → LlamaIndex TextNode
        → OpenAI/HuggingFace Embeddings
          → PGVectorStore (PostgreSQL + pgvector extension)
            → VectorStoreIndex
              → VectorIndexRetriever (top-k similarity)

    Each VectorKnowledgeIndex owns one pgvector table.
    Naming convention: civitas_vectors_{space_name}
    """

    def __init__(
        self,
        config: IndexConfig,
        connection_string: str,
        embed_model: Optional[Any] = None,
    ) -> None:
        super().__init__(config)
        self.connection_string = connection_string
        self.embed_model = embed_model
        self._index: Optional[Any] = None           # LlamaIndex VectorStoreIndex
        self._vector_store: Optional[Any] = None    # LlamaIndex PGVectorStore

        # Derive table name from space
        if not config.vector_table_name:
            space = config.knowledge_space_name or "global"
            config.vector_table_name = f"civitas_vectors_{space.replace('-', '_')}"

    def _get_vector_store(self) -> Any:
        """Lazy-initialize the PGVectorStore."""
        if self._vector_store is None:
            try:
                from llama_index.vector_stores.postgres import PGVectorStore
                self._vector_store = PGVectorStore.from_params(
                    database=self._parse_db_name(),
                    host=self._parse_host(),
                    password=self._parse_password(),
                    port=self._parse_port(),
                    user=self._parse_user(),
                    table_name=self.config.vector_table_name,
                    embed_dim=self.config.embedding_dimensions,
                    hybrid_search=True,          # Enable BM25 fallback in pgvector
                    text_search_config="english",
                )
            except ImportError:
                raise ImportError(
                    "llama-index-vector-stores-postgres required: "
                    "pip install llama-index-vector-stores-postgres"
                )
        return self._vector_store

    def _get_embed_model(self) -> Any:
        """Return embedding model, defaulting to OpenAI if not provided."""
        if self.embed_model:
            return self.embed_model
        try:
            from llama_index.embeddings.openai import OpenAIEmbedding
            return OpenAIEmbedding(model=self.config.embedding_model)
        except ImportError:
            from llama_index.core.embeddings import resolve_embed_model
            return resolve_embed_model("default")

    # ── Core Operations ────────────────────────────────────────

    def build(self, nodes: list[Any]) -> None:
        """Build the vector index from a list of LlamaIndex TextNodes."""
        self._mark_building()
        try:
            from llama_index.core import VectorStoreIndex, StorageContext
            vector_store = self._get_vector_store()
            storage_context = StorageContext.from_defaults(vector_store=vector_store)
            self._index = VectorStoreIndex(
                nodes=nodes,
                storage_context=storage_context,
                embed_model=self._get_embed_model(),
                show_progress=True,
            )
            self.config.node_count = len(nodes)
            self._mark_ready()
        except Exception as exc:
            self._mark_error(IndexBuildError(self.index_id, str(exc)))

    def add_nodes(self, nodes: list[Any]) -> None:
        """Incrementally add nodes to the existing index."""
        if self._index is None:
            # If no in-memory index, create from vector store directly
            self._ensure_index()
        try:
            for node in nodes:
                self._index.insert_nodes([node])
            self.config.node_count += len(nodes)
            self.config.status = IndexStatus.READY
            logger.debug(
                "VectorIndex: added %d nodes (total: %d)", len(nodes), self.config.node_count
            )
        except Exception as exc:
            logger.error("Failed to add nodes to vector index: %s", exc)
            raise

    def delete_node(self, node_id: str) -> None:
        """Remove a node by its ID."""
        if self._index is None:
            self._ensure_index()
        try:
            self._index.delete_nodes([node_id])
            self.config.node_count = max(0, self.config.node_count - 1)
        except Exception as exc:
            logger.warning("Failed to delete node %s: %s", node_id, exc)

    def get_retriever(
        self,
        top_k: int = 10,
        filters: Optional[Any] = None,
        **kwargs: Any,
    ) -> Any:
        """Return a VectorIndexRetriever configured for this index."""
        if self._index is None:
            self._ensure_index()
        if self._index is None:
            raise IndexNotBuiltError(self.index_id)
        try:
            from llama_index.core.retrievers import VectorIndexRetriever
            return VectorIndexRetriever(
                index=self._index,
                similarity_top_k=top_k,
                filters=filters,
                **kwargs,
            )
        except ImportError:
            return self._index.as_retriever(similarity_top_k=top_k, filters=filters)

    # ── Internal Helpers ───────────────────────────────────────

    def _ensure_index(self) -> None:
        """Load or create the VectorStoreIndex from the existing vector store."""
        try:
            from llama_index.core import VectorStoreIndex, StorageContext
            vector_store = self._get_vector_store()
            storage_context = StorageContext.from_defaults(vector_store=vector_store)
            self._index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                storage_context=storage_context,
                embed_model=self._get_embed_model(),
            )
            self.config.status = IndexStatus.READY
        except Exception as exc:
            logger.error("Failed to load vector index: %s", exc)

    def _parse_db_name(self) -> str:
        return self.connection_string.split("/")[-1]

    def _parse_host(self) -> str:
        # postgresql://user:pass@host:port/db
        return self.connection_string.split("@")[1].split(":")[0]

    def _parse_port(self) -> int:
        try:
            return int(self.connection_string.split("@")[1].split(":")[1].split("/")[0])
        except (IndexError, ValueError):
            return 5432

    def _parse_user(self) -> str:
        return self.connection_string.split("://")[1].split(":")[0]

    def _parse_password(self) -> str:
        return self.connection_string.split("://")[1].split(":")[1].split("@")[0]

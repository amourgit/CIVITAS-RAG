"""
civitas.indexing.registry
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IndexRegistry — tracks all live indexes across all knowledge spaces.

One registry per application instance.
Each KnowledgeSpace has up to N indexes (one per type it needs).
The registry resolves indexes by (space_name, index_type).

The registry also serves as the main entry point for
adding DocumentChunks to all relevant indexes in one call.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from civitas.core.exceptions.knowledge_exceptions import IndexNotFoundError
from civitas.core.models.chunk import DocumentChunk
from civitas.indexing.indexes.base import BaseKnowledgeIndex, IndexConfig, IndexType

logger = logging.getLogger(__name__)


def _chunk_to_llamaindex_node(chunk: DocumentChunk) -> object:
    """Convert a DocumentChunk to a LlamaIndex TextNode."""
    try:
        from llama_index.core.schema import TextNode
        return TextNode(
            text=chunk.content,
            id_=str(chunk.id),
            metadata=chunk.to_llamaindex_node_metadata(),
        )
    except ImportError:
        # Return a simple object if LlamaIndex not installed
        class _FakeNode:
            def __init__(self, chunk: DocumentChunk) -> None:
                self.node_id = str(chunk.id)
                self._text = chunk.content
                self.metadata = chunk.to_llamaindex_node_metadata()
            def get_content(self) -> str:
                return self._text
        return _FakeNode(chunk)


class IndexRegistry:
    """
    Central registry for all knowledge indexes.

    Maintains a mapping of:
      (knowledge_space_id or 'global', IndexType) → BaseKnowledgeIndex

    Provides:
      · add_chunks(chunks, space_id)    — fan-out to all relevant indexes
      · get_index(space_id, type)       — retrieve specific index
      · list_indexes(space_id)          — list all indexes for a space
    """

    def __init__(self) -> None:
        # key: (space_key, index_type) → index
        self._indexes: dict[tuple[str, IndexType], BaseKnowledgeIndex] = {}
        logger.info("IndexRegistry initialized.")

    # ── Registration ───────────────────────────────────────────

    def register(
        self,
        index: BaseKnowledgeIndex,
        space_key: Optional[str] = None,
    ) -> None:
        """
        Register an index.
        space_key: knowledge space name or ID. Use 'global' for cross-space indexes.
        """
        key = (space_key or "global", index.index_type)
        self._indexes[key] = index
        logger.info(
            "Registered index: type=%s, space=%s, id=%s",
            index.index_type.value, space_key, index.index_id[:8],
        )

    def unregister(self, space_key: str, index_type: IndexType) -> None:
        key = (space_key, index_type)
        if key in self._indexes:
            del self._indexes[key]
            logger.info("Unregistered index: type=%s, space=%s", index_type.value, space_key)

    # ── Lookup ─────────────────────────────────────────────────

    def get_index(
        self,
        index_type: IndexType,
        space_key: Optional[str] = None,
    ) -> BaseKnowledgeIndex:
        """Get a specific index or raise IndexNotFoundError."""
        key = (space_key or "global", index_type)
        idx = self._indexes.get(key)
        if not idx:
            raise IndexNotFoundError(f"{index_type.value}:{space_key or 'global'}")
        return idx

    def get_index_safe(
        self,
        index_type: IndexType,
        space_key: Optional[str] = None,
    ) -> Optional[BaseKnowledgeIndex]:
        """Get a specific index or return None."""
        key = (space_key or "global", index_type)
        return self._indexes.get(key)

    def list_indexes(self, space_key: str) -> list[BaseKnowledgeIndex]:
        """List all indexes registered for a given space."""
        return [
            idx for (sk, _), idx in self._indexes.items()
            if sk == space_key
        ]

    # ── Bulk Operations ────────────────────────────────────────

    def add_chunks(
        self,
        chunks: list[DocumentChunk],
        knowledge_space_id: str,
    ) -> dict[str, int]:
        """
        Add document chunks to all indexes registered for a knowledge space.

        Returns a dict of {index_type: nodes_added}.
        Skips indexes that are not registered or not ready.
        """
        if not chunks:
            return {}

        nodes = [_chunk_to_llamaindex_node(chunk) for chunk in chunks]
        results: dict[str, int] = {}

        for (sk, itype), idx in self._indexes.items():
            if sk not in (knowledge_space_id, "global"):
                continue
            try:
                idx.add_nodes(nodes)
                results[itype.value] = len(nodes)
                logger.debug(
                    "Added %d nodes to %s index [space=%s]",
                    len(nodes), itype.value, sk,
                )
            except Exception as exc:
                logger.error(
                    "Failed to add nodes to %s index [space=%s]: %s",
                    itype.value, sk, exc,
                )
                results[itype.value] = 0

        return results

    def delete_document_nodes(
        self,
        chunk_ids: list[UUID],
        knowledge_space_id: str,
    ) -> None:
        """Remove all nodes for given chunk IDs from all indexes."""
        for chunk_id in chunk_ids:
            for (sk, _), idx in self._indexes.items():
                if sk in (knowledge_space_id, "global"):
                    try:
                        idx.delete_node(str(chunk_id))
                    except Exception as exc:
                        logger.warning("Failed to delete node %s: %s", chunk_id, exc)

    def rebuild_index(
        self,
        index_type: IndexType,
        space_key: str,
        all_chunks: list[DocumentChunk],
    ) -> None:
        """Rebuild a specific index from scratch using all provided chunks."""
        idx = self.get_index_safe(index_type, space_key)
        if not idx:
            logger.warning("No index found for type=%s space=%s", index_type.value, space_key)
            return
        nodes = [_chunk_to_llamaindex_node(chunk) for chunk in all_chunks]
        idx.build(nodes)

    # ── Introspection ──────────────────────────────────────────

    def summary(self) -> list[dict]:
        return [
            {
                "space": sk,
                "type": itype.value,
                "index_id": idx.index_id[:8],
                "status": idx.config.status.value,
                "nodes": idx.config.node_count,
            }
            for (sk, itype), idx in self._indexes.items()
        ]

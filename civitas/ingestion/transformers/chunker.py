"""
civitas.ingestion.transformers.chunker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Document chunking strategies.

The chunker transforms a parsed Document (full text) into a list
of DocumentChunk objects ready for embedding and indexing.

Strategies:
  · SemanticChunker   — LlamaIndex SemanticSplitterNodeParser
  · SentenceChunker   — LlamaIndex SentenceSplitter (fast, deterministic)
  · FixedSizeChunker  — Simple token-window chunking
  · HierarchicalChunker — Large → small chunk hierarchy

All chunkers populate the inter-chunk linkage (prev/next)
and inherit metadata from the parent Document.
"""

from __future__ import annotations

import abc
import logging
from typing import Optional
from uuid import uuid4

from civitas.core.models.chunk import ChunkType, DocumentChunk
from civitas.core.models.document import Document

logger = logging.getLogger(__name__)


class BaseChunker(abc.ABC):
    """Abstract base chunker."""

    @abc.abstractmethod
    def chunk(self, document: Document) -> list[DocumentChunk]:
        """Split a Document into DocumentChunks."""

    def _build_chunk(
        self,
        document: Document,
        text: str,
        index: int,
        chunk_type: ChunkType,
        page_number: Optional[int] = None,
        section_heading: Optional[str] = None,
        extra_metadata: Optional[dict] = None,
    ) -> DocumentChunk:
        """Factory method for creating a DocumentChunk from a Document."""
        meta = document.metadata
        ac = meta.access_control
        chunk = DocumentChunk(
            id=uuid4(),
            document_id=document.id,
            document_title=document.title,
            document_version=document.version,
            chunk_index=index,
            chunk_type=chunk_type,
            content=text,
            page_number=page_number,
            section_heading=section_heading,
            # Inherited metadata
            domain=meta.domain,
            subdomain=meta.subdomain,
            category=meta.category,
            knowledge_space_id=meta.knowledge_space_id,
            knowledge_space_name=meta.knowledge_space_name,
            taxonomy_path=list(meta.taxonomy_path),
            tags=list(meta.tags),
            classification_level=ac.classification_level.value,
            access_level=ac.access_level.value,
            allowed_teams=list(ac.allowed_teams),
            allowed_roles=list(ac.allowed_roles),
            allowed_agent_ids=list(ac.allowed_agent_ids),
            extra_metadata=extra_metadata or {},
        )
        chunk.compute_hash()
        return chunk

    @staticmethod
    def _link_chunks(chunks: list[DocumentChunk]) -> None:
        """Set prev/next linkage on a list of ordered chunks."""
        for i, chunk in enumerate(chunks):
            if i > 0:
                chunk.prev_chunk_id = chunks[i - 1].id
            if i < len(chunks) - 1:
                chunk.next_chunk_id = chunks[i + 1].id


class SentenceChunker(BaseChunker):
    """
    LlamaIndex SentenceSplitter-based chunker.
    Fast, deterministic, token-aware splitting.
    Default chunker for most knowledge spaces.
    """

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, document: Document) -> list[DocumentChunk]:
        if not document.content:
            logger.warning("Document %s has no content to chunk.", document.id)
            return []

        try:
            from llama_index.core.node_parser import SentenceSplitter
            splitter = SentenceSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )
            from llama_index.core.schema import Document as LlamaDoc
            llama_doc = LlamaDoc(text=document.content, doc_id=str(document.id))
            nodes = splitter.get_nodes_from_documents([llama_doc])
        except ImportError:
            # Fallback: simple fixed-size char splitter
            nodes = self._simple_split(document.content)
            return self._nodes_to_chunks_fallback(document, nodes)

        chunks = []
        for i, node in enumerate(nodes):
            chunk = self._build_chunk(
                document=document,
                text=node.get_content(),
                index=i,
                chunk_type=ChunkType.SENTENCE,
            )
            chunks.append(chunk)
        self._link_chunks(chunks)
        logger.debug("SentenceChunker: %d chunks from document %s", len(chunks), document.id)
        return chunks

    def _simple_split(self, text: str) -> list[str]:
        """Fallback: simple character-based split."""
        size = self.chunk_size * 4   # ~4 chars per token
        overlap = self.chunk_overlap * 4
        parts = []
        start = 0
        while start < len(text):
            end = start + size
            parts.append(text[start:end])
            start = end - overlap
        return parts

    def _nodes_to_chunks_fallback(self, document: Document, texts: list[str]) -> list[DocumentChunk]:
        chunks = [
            self._build_chunk(document, t, i, ChunkType.FIXED_SIZE)
            for i, t in enumerate(texts)
        ]
        self._link_chunks(chunks)
        return chunks


class SemanticChunker(BaseChunker):
    """
    LlamaIndex SemanticSplitterNodeParser.
    Splits on semantic boundaries using embedding similarity.
    More expensive than SentenceChunker but produces higher-quality chunks.
    Recommended for GraphRAG pipelines.
    """

    def __init__(
        self,
        embed_model: Optional[object] = None,
        breakpoint_percentile_threshold: int = 95,
        buffer_size: int = 1,
    ) -> None:
        self.embed_model = embed_model
        self.breakpoint_percentile_threshold = breakpoint_percentile_threshold
        self.buffer_size = buffer_size

    def chunk(self, document: Document) -> list[DocumentChunk]:
        if not document.content:
            return []

        try:
            from llama_index.core.node_parser import SemanticSplitterNodeParser
            from llama_index.core.schema import Document as LlamaDoc

            splitter = SemanticSplitterNodeParser(
                embed_model=self.embed_model,
                breakpoint_percentile_threshold=self.breakpoint_percentile_threshold,
                buffer_size=self.buffer_size,
            )
            llama_doc = LlamaDoc(text=document.content, doc_id=str(document.id))
            nodes = splitter.get_nodes_from_documents([llama_doc])
        except Exception as exc:
            logger.warning("SemanticChunker failed, falling back to SentenceChunker: %s", exc)
            return SentenceChunker().chunk(document)

        chunks = [
            self._build_chunk(document, node.get_content(), i, ChunkType.SEMANTIC)
            for i, node in enumerate(nodes)
        ]
        self._link_chunks(chunks)
        return chunks


class HierarchicalChunker(BaseChunker):
    """
    Two-level hierarchical chunking.
    Produces large parent chunks + small child chunks.
    Enables parent-document retrieval in LlamaIndex.
    Recommended for complex, long-form documents.
    """

    def __init__(
        self,
        parent_chunk_size: int = 2048,
        child_chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> None:
        self.parent_chunk_size = parent_chunk_size
        self.child_chunk_size = child_chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, document: Document) -> list[DocumentChunk]:
        if not document.content:
            return []
        try:
            from llama_index.core.node_parser import HierarchicalNodeParser
            from llama_index.core.schema import Document as LlamaDoc

            parser = HierarchicalNodeParser.from_defaults(
                chunk_sizes=[self.parent_chunk_size, self.child_chunk_size],
            )
            llama_doc = LlamaDoc(text=document.content, doc_id=str(document.id))
            nodes = parser.get_nodes_from_documents([llama_doc])
        except Exception as exc:
            logger.warning("HierarchicalChunker failed, falling back: %s", exc)
            return SentenceChunker(self.child_chunk_size, self.chunk_overlap).chunk(document)

        chunks = [
            self._build_chunk(document, node.get_content(), i, ChunkType.SECTION)
            for i, node in enumerate(nodes)
        ]
        self._link_chunks(chunks)
        return chunks

"""
civitas.graphrag.query
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GraphRAG Query Engine — local and global graph-enhanced retrieval.

Two query modes (Microsoft GraphRAG pattern):

  LOCAL  — entity-focused
    · Identify entities mentioned in the query
    · Retrieve entity subgraph (direct neighbors)
    · Fetch chunks associated with those entities
    · Augment vector retrieval results

  GLOBAL — theme-focused
    · Match query against community summaries
    · Return community summaries as context
    · Best for broad questions ("What are the main topics?")

The GraphRAGQueryEngine is consumed by the RetrievalEngine
when IndexStrategy.GRAPH_ENHANCED or FULL is selected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from civitas.graphrag.builder import Community, KnowledgeGraph

logger = logging.getLogger(__name__)


class GraphQueryMode(str, Enum):
    LOCAL = "local"
    GLOBAL = "global"
    HYBRID = "hybrid"


@dataclass
class GraphQueryResult:
    """Result from a graph query."""
    mode: GraphQueryMode
    matched_entities: list[str] = field(default_factory=list)
    related_entities: list[str] = field(default_factory=list)
    community_summaries: list[str] = field(default_factory=list)
    related_chunk_ids: list[str] = field(default_factory=list)
    context: str = ""


class GraphRAGQueryEngine:
    """
    Graph-enhanced query engine for the CIVITAS platform.

    Augments standard vector retrieval with graph-structured context:
      · For narrow queries: identify entities, traverse graph, collect chunks
      · For broad queries:  match community summaries, return thematic context

    Usage:
        engine = GraphRAGQueryEngine(knowledge_graph=kg)
        result = engine.query("What are the obligations under the NDA?", mode=GraphQueryMode.LOCAL)
    """

    def __init__(
        self,
        knowledge_graph: KnowledgeGraph,
        embed_model: Optional[Any] = None,
        max_hops: int = 2,
        max_entities: int = 20,
        max_communities: int = 5,
    ) -> None:
        self.kg = knowledge_graph
        self.embed_model = embed_model
        self.max_hops = max_hops
        self.max_entities = max_entities
        self.max_communities = max_communities

    def query(
        self,
        query_text: str,
        mode: GraphQueryMode = GraphQueryMode.LOCAL,
    ) -> GraphQueryResult:
        """Execute a graph-enhanced query."""
        if mode == GraphQueryMode.LOCAL:
            return self._local_query(query_text)
        elif mode == GraphQueryMode.GLOBAL:
            return self._global_query(query_text)
        else:
            # Hybrid: run both, merge
            local = self._local_query(query_text)
            global_ = self._global_query(query_text)
            return GraphQueryResult(
                mode=GraphQueryMode.HYBRID,
                matched_entities=local.matched_entities,
                related_entities=local.related_entities,
                community_summaries=global_.community_summaries,
                related_chunk_ids=list(set(local.related_chunk_ids)),
                context=f"{local.context}\n\n{global_.context}".strip(),
            )

    def _local_query(self, query_text: str) -> GraphQueryResult:
        """
        Local query: find entities in query → traverse graph → collect chunks.
        """
        if self.kg.graph is None:
            return GraphQueryResult(mode=GraphQueryMode.LOCAL)

        # Step 1: Match query words to entity names (simple keyword match)
        query_tokens = set(query_text.lower().split())
        matched: list[str] = []
        for entity_name in self.kg.entities:
            entity_tokens = set(entity_name.lower().split())
            if query_tokens & entity_tokens:
                matched.append(entity_name)

        if not matched:
            # Fallback: return most-connected entities
            matched = sorted(
                self.kg.entities.keys(),
                key=lambda n: self.kg.graph.degree(n) if n in self.kg.graph else 0,
                reverse=True,
            )[:5]

        # Step 2: BFS hop traversal
        visited: set[str] = set(matched)
        frontier = set(matched)
        for _ in range(self.max_hops):
            next_frontier: set[str] = set()
            for node in frontier:
                if node in self.kg.graph:
                    for neighbor in self.kg.graph.neighbors(node):
                        if neighbor not in visited:
                            next_frontier.add(neighbor)
                            visited.add(neighbor)
            frontier = next_frontier
            if len(visited) >= self.max_entities:
                break

        related = [n for n in visited if n not in set(matched)]

        # Step 3: Collect chunk IDs from matched entities
        chunk_ids: list[str] = []
        for name in visited:
            entity = self.kg.entities.get(name)
            if entity:
                chunk_ids.extend(entity.source_chunk_ids)
        chunk_ids = list(set(chunk_ids))

        # Build context string
        context_parts = []
        for name in matched[:5]:
            entity = self.kg.entities.get(name)
            if entity and entity.description:
                context_parts.append(f"**{name}** ({entity.entity_type}): {entity.description}")

        return GraphQueryResult(
            mode=GraphQueryMode.LOCAL,
            matched_entities=matched[:10],
            related_entities=related[:10],
            related_chunk_ids=chunk_ids[:50],
            context="\n".join(context_parts),
        )

    def _global_query(self, query_text: str) -> GraphQueryResult:
        """
        Global query: match query against community summaries.
        Returns thematic summaries for broad, cross-cutting questions.
        """
        if not self.kg.communities:
            return GraphQueryResult(mode=GraphQueryMode.GLOBAL)

        query_tokens = set(query_text.lower().split())
        scored_communities: list[tuple[float, Community]] = []

        for community in self.kg.communities:
            if not community.summary:
                continue
            summary_tokens = set(community.summary.lower().split())
            overlap = len(query_tokens & summary_tokens)
            if overlap > 0:
                score = overlap / max(len(query_tokens), 1)
                scored_communities.append((score, community))

        scored_communities.sort(key=lambda x: x[0], reverse=True)
        top_communities = [c for _, c in scored_communities[:self.max_communities]]

        if not top_communities and self.kg.communities:
            # Fallback: return largest communities
            top_communities = sorted(
                self.kg.communities,
                key=lambda c: len(c.entities),
                reverse=True,
            )[:self.max_communities]

        summaries = [
            c.summary for c in top_communities if c.summary
        ]
        chunk_ids: list[str] = []
        for community in top_communities:
            chunk_ids.extend(community.source_chunk_ids)

        context = "\n\n".join([
            f"**Community: {c.title or f'Group {c.community_id}'}**\n{c.summary}"
            for c in top_communities
            if c.summary
        ])

        return GraphQueryResult(
            mode=GraphQueryMode.GLOBAL,
            community_summaries=summaries,
            related_chunk_ids=list(set(chunk_ids))[:50],
            context=context,
        )

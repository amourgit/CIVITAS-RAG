"""
civitas.graphrag.builder
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GraphRAG Builder — constructs the knowledge graph from extracted entities/relations
and detects communities using the Leiden algorithm.

Architecture:
  ExtractionResult → NetworkX Graph → Community Detection → Community Summaries
                                                          ↓
                                              PostgreSQL Graph Store

Community summaries enable "global" queries that span the entire knowledge space
without needing to retrieve individual chunks.

Two retrieval modes (following Microsoft GraphRAG):
  · LOCAL  — entity-centric, retrieves relevant entity subgraphs
  · GLOBAL — community-centric, uses pre-computed community summaries
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from civitas.graphrag.extractor import Entity, ExtractionResult, Relation

logger = logging.getLogger(__name__)


@dataclass
class Community:
    """A detected community of related entities."""
    community_id: int
    entities: list[str]         # Entity names
    title: Optional[str] = None
    summary: Optional[str] = None
    level: int = 0
    weight: float = 1.0
    source_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class KnowledgeGraph:
    """In-memory knowledge graph for a knowledge space."""
    space_name: str
    entities: dict[str, Entity] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)
    communities: list[Community] = field(default_factory=list)
    graph: Optional[Any] = None     # networkx.Graph

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    @property
    def relation_count(self) -> int:
        return len(self.relations)

    @property
    def community_count(self) -> int:
        return len(self.communities)


class KnowledgeGraphBuilder:
    """
    Builds a knowledge graph from ExtractionResult objects.

    Steps:
      1. Accumulate entities (merge duplicates by name)
      2. Build NetworkX graph (entities = nodes, relations = weighted edges)
      3. Detect communities using graspologic Leiden algorithm
      4. Generate community summaries (optional, uses LLM)
    """

    def __init__(
        self,
        llm: Optional[Any] = None,
        min_edge_weight: float = 0.5,
        max_community_size: int = 10,
        generate_summaries: bool = True,
    ) -> None:
        self.llm = llm
        self.min_edge_weight = min_edge_weight
        self.max_community_size = max_community_size
        self.generate_summaries = generate_summaries

    def build(
        self,
        extraction_results: list[ExtractionResult],
        space_name: str,
    ) -> KnowledgeGraph:
        """Build a complete KnowledgeGraph from extraction results."""
        kg = KnowledgeGraph(space_name=space_name)

        # Step 1: Merge all entities
        for result in extraction_results:
            for entity in result.entities:
                if entity.name in kg.entities:
                    existing = kg.entities[entity.name]
                    existing.source_chunk_ids.extend(entity.source_chunk_ids)
                else:
                    kg.entities[entity.name] = entity
            kg.relations.extend(result.relations)

        # Step 2: Build NetworkX graph
        kg.graph = self._build_networkx_graph(kg)
        logger.info(
            "Graph built: %d nodes, %d edges [space=%s]",
            kg.graph.number_of_nodes(), kg.graph.number_of_edges(), space_name,
        )

        # Step 3: Community detection
        if kg.graph.number_of_nodes() >= 2:
            kg.communities = self._detect_communities(kg.graph, kg.entities)
            logger.info("Detected %d communities.", len(kg.communities))

        # Step 4: Generate summaries
        if self.generate_summaries and self.llm and kg.communities:
            self._generate_community_summaries(kg)

        return kg

    def _build_networkx_graph(self, kg: KnowledgeGraph) -> Any:
        """Construct a weighted undirected NetworkX graph."""
        try:
            import networkx as nx
        except ImportError:
            raise ImportError("networkx required: pip install networkx")

        G = nx.Graph()

        # Add entity nodes
        for name, entity in kg.entities.items():
            G.add_node(name,
                       entity_type=entity.entity_type,
                       description=entity.description or "",
                       chunk_count=len(entity.source_chunk_ids))

        # Add relation edges (accumulate weights for repeated pairs)
        edge_weights: dict[tuple[str, str], float] = {}
        edge_data: dict[tuple[str, str], list[str]] = {}

        for rel in kg.relations:
            if rel.source not in kg.entities or rel.target not in kg.entities:
                continue
            key = tuple(sorted([rel.source, rel.target]))
            edge_weights[key] = edge_weights.get(key, 0) + rel.weight
            if key not in edge_data:
                edge_data[key] = []
            edge_data[key].append(rel.relation_type)

        for (src, tgt), weight in edge_weights.items():
            if weight >= self.min_edge_weight:
                G.add_edge(src, tgt,
                           weight=weight,
                           relation_types=list(set(edge_data.get((src, tgt), []))))

        return G

    def _detect_communities(
        self,
        graph: Any,
        entities: dict[str, Entity],
    ) -> list[Community]:
        """Detect communities using Leiden or Louvain algorithm."""
        communities: list[Community] = []

        try:
            from graspologic.partition import leiden
            import numpy as np

            adjacency = {}
            node_list = list(graph.nodes())
            for i, src in enumerate(node_list):
                for j, tgt in enumerate(node_list):
                    if graph.has_edge(src, tgt):
                        w = graph[src][tgt].get("weight", 1.0)
                        adjacency[(i, j)] = w

            partition = leiden(graph)
            cluster_map: dict[int, list[str]] = {}
            for node, cluster_id in zip(node_list, partition):
                cluster_map.setdefault(cluster_id, []).append(node)

            for cid, members in cluster_map.items():
                chunk_ids: list[str] = []
                for name in members:
                    if name in entities:
                        chunk_ids.extend(entities[name].source_chunk_ids)
                communities.append(Community(
                    community_id=cid,
                    entities=members,
                    source_chunk_ids=list(set(chunk_ids)),
                    level=0,
                ))

        except ImportError:
            # Fallback: connected components as communities
            import networkx as nx
            for i, component in enumerate(nx.connected_components(graph)):
                members = list(component)
                chunk_ids = []
                for name in members:
                    if name in entities:
                        chunk_ids.extend(entities[name].source_chunk_ids)
                communities.append(Community(
                    community_id=i,
                    entities=members,
                    source_chunk_ids=list(set(chunk_ids)),
                ))

        return communities

    def _generate_community_summaries(self, kg: KnowledgeGraph) -> None:
        """Use LLM to generate a descriptive summary for each community."""
        SUMMARY_PROMPT = """You are summarizing a knowledge community.

Community entities: {entities}
Key relationships: {relations}

Write a concise 2-3 sentence summary describing:
1. What these entities represent collectively
2. The main theme or topic connecting them
3. Why they are related

Summary:"""

        for community in kg.communities:
            try:
                # Collect relations within community
                community_set = set(community.entities)
                related = [
                    f"{r.source} —[{r.relation_type}]→ {r.target}"
                    for r in kg.relations
                    if r.source in community_set and r.target in community_set
                ][:10]

                prompt = SUMMARY_PROMPT.format(
                    entities=", ".join(community.entities[:20]),
                    relations="\n".join(related) or "none detected",
                )
                response = self.llm.complete(prompt)
                community.summary = response.text.strip()
                community.title = community.entities[0] if community.entities else f"Community {community.community_id}"
            except Exception as exc:
                logger.warning("Failed to summarize community %d: %s", community.community_id, exc)

"""
civitas.access.contracts.query
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Typed contracts for the Knowledge Access Layer.

These contracts define the API surface that future LangGraph agents
will use to interact with the knowledge platform.
All internal complexity is hidden behind these contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class QueryIntent(str, Enum):
    """Agent-declared query intent — influences retrieval strategy selection."""
    FACTUAL = "factual"           # Find a specific fact
    SUMMARY = "summary"           # Summarize a topic or document
    COMPARISON = "comparison"     # Compare two or more items
    EXPLORATION = "exploration"   # Discover what knowledge exists
    VERIFICATION = "verification" # Verify or contradict a claim
    PROCEDURAL = "procedural"     # Find steps or procedures
    REGULATORY = "regulatory"     # Find rules, policies, compliance info


@dataclass
class KnowledgeQuery:
    """
    The typed query contract for the Knowledge Access Layer.

    This is what LangGraph agent tools will submit to the knowledge platform.
    It abstracts all retrieval complexity behind a clean interface.
    """
    # The query itself
    query: str
    intent: QueryIntent = QueryIntent.FACTUAL

    # Scope
    knowledge_space: Optional[str] = None          # None = search all accessible spaces
    domains: list[str] = field(default_factory=list)
    taxonomy_paths: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    date_from: Optional[str] = None               # ISO date string
    date_to: Optional[str] = None

    # Retrieval config
    top_k: int = 10
    min_score: float = 0.0
    include_graph_context: bool = False
    include_summaries: bool = False

    # Agent identity (for access control)
    agent_id: Optional[str] = None
    requesting_team: Optional[str] = None
    requesting_role: Optional[str] = None

    # Response format
    include_metadata: bool = True
    include_full_content: bool = False             # False = snippet only
    max_content_chars: int = 500


@dataclass
class KnowledgeResult:
    """A single knowledge result returned to an agent."""
    chunk_id: str
    document_id: str
    document_title: str
    content: str                    # Full or snippet, depending on include_full_content
    score: float
    domain: str
    category: str
    knowledge_space: Optional[str]
    taxonomy_path: str
    page_number: Optional[int]
    section: Optional[str]
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_context_block(self) -> str:
        """Format as a context block for LLM prompts."""
        lines = [
            f"[SOURCE: {self.document_title}",
            f" | Space: {self.knowledge_space or 'global'}",
            f" | Domain: {self.domain}/{self.category}",
        ]
        if self.page_number:
            lines.append(f" | Page: {self.page_number}")
        if self.section:
            lines.append(f" | Section: {self.section}")
        lines.append("]")
        lines.append(self.content)
        return "".join(lines[:1]) + " ".join(lines[1:-1]) + lines[-1] + "\n\n" + lines[-1]


@dataclass
class KnowledgeResponse:
    """
    Complete typed response from the Knowledge Access Layer.
    This is what agents receive from their knowledge tool calls.
    """
    query: str
    intent: QueryIntent
    results: list[KnowledgeResult]
    total_found: int
    knowledge_space: Optional[str]
    graph_context: Optional[str] = None       # GraphRAG context if requested
    latency_ms: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def has_results(self) -> bool:
        return len(self.results) > 0

    @property
    def top_result(self) -> Optional[KnowledgeResult]:
        return self.results[0] if self.results else None

    def to_context_string(self, max_results: int = 5) -> str:
        """
        Format all results as a context string for LLM prompts.
        This is the primary output format consumed by LangGraph agents.
        """
        if not self.results:
            return f"No relevant knowledge found for: '{self.query}'"

        parts = [f"KNOWLEDGE CONTEXT for: '{self.query}'\n{'='*60}\n"]

        for i, result in enumerate(self.results[:max_results], 1):
            parts.append(
                f"\n[{i}] {result.document_title}\n"
                f"    Domain: {result.domain} > {result.category}\n"
                f"    Score: {result.score:.3f}\n"
                f"    {result.content[:500]}{'...' if len(result.content) > 500 else ''}\n"
            )

        if self.graph_context:
            parts.append(f"\n{'='*60}\nGRAPH CONTEXT:\n{self.graph_context}\n")

        return "".join(parts)

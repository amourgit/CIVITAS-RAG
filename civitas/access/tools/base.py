"""
civitas.access.tools.base
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Base Tool Interfaces — the bridge between CIVITAS and LangGraph agents.

These abstract base classes define the contract that future
LangGraph agent tools must implement to access knowledge.

Design:
  · Tools are thin wrappers over the UnifiedKnowledgeEngine
  · Each tool represents ONE capability (search, summarize, compare…)
  · Tools declare their own access requirements (space, domain)
  · Tools format output as strings ready for LLM consumption

LangGraph integration (future):
  @tool
  def search_legal_contracts(query: str) -> str:
      return LegalContractSearchTool(engine=engine).run(query)

This module does NOT import LangGraph — it only defines the interface.
LangGraph is a future consumer, not a current dependency.
"""

from __future__ import annotations

import abc
import logging
from typing import Any, Optional

from civitas.access.contracts.query import KnowledgeQuery, KnowledgeResponse, QueryIntent

logger = logging.getLogger(__name__)


class BaseKnowledgeTool(abc.ABC):
    """
    Abstract base for all CIVITAS knowledge tools.

    Each tool:
      · Targets a specific knowledge space and/or domain
      · Has a declared intent that influences retrieval strategy
      · Exposes a single run() method returning a formatted string
      · Handles errors gracefully (never raises to LLM)
    """

    @property
    @abc.abstractmethod
    def tool_name(self) -> str:
        """Unique tool identifier (used as LangGraph tool name)."""

    @property
    @abc.abstractmethod
    def tool_description(self) -> str:
        """Human-readable description for LLM tool selection."""

    @property
    def default_knowledge_space(self) -> Optional[str]:
        """The knowledge space this tool primarily targets."""
        return None

    @property
    def default_intent(self) -> QueryIntent:
        return QueryIntent.FACTUAL

    @property
    def default_top_k(self) -> int:
        return 5

    @abc.abstractmethod
    def _build_query(self, user_query: str, **kwargs: Any) -> KnowledgeQuery:
        """Build a KnowledgeQuery from user input."""

    def run(self, user_query: str, **kwargs: Any) -> str:
        """
        Execute the tool and return a formatted string for the LLM.
        Never raises — errors are returned as informative strings.
        """
        try:
            knowledge_query = self._build_query(user_query, **kwargs)
            response = self._get_engine().query(knowledge_query)
            return self._format_response(response, user_query)
        except Exception as exc:
            logger.error("Tool '%s' failed: %s", self.tool_name, exc)
            return f"[CIVITAS TOOL ERROR] '{self.tool_name}' failed: {exc}"

    @abc.abstractmethod
    def _get_engine(self) -> Any:
        """Return the UnifiedKnowledgeEngine instance."""

    def _format_response(self, response: KnowledgeResponse, query: str) -> str:
        """Format a KnowledgeResponse as a string. Override for custom formatting."""
        return response.to_context_string(max_results=self.default_top_k)


class DomainSearchTool(BaseKnowledgeTool):
    """
    Generic domain search tool.
    Can be instantiated for any knowledge space and domain.

    Usage:
        tool = DomainSearchTool(
            engine=unified_engine,
            name="search_legal_contracts",
            description="Search the legal contracts knowledge base for contract terms, obligations, and clauses.",
            knowledge_space="legal-contracts",
            domain="legal",
            intent=QueryIntent.FACTUAL,
        )
    """

    def __init__(
        self,
        engine: Any,
        name: str,
        description: str,
        knowledge_space: str,
        domain: Optional[str] = None,
        intent: QueryIntent = QueryIntent.FACTUAL,
        top_k: int = 5,
        include_graph_context: bool = False,
        agent_id: Optional[str] = None,
    ) -> None:
        self._engine = engine
        self._name = name
        self._description = description
        self._knowledge_space = knowledge_space
        self._domain = domain
        self._intent = intent
        self._top_k = top_k
        self._include_graph_context = include_graph_context
        self._agent_id = agent_id

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_description(self) -> str:
        return self._description

    @property
    def default_knowledge_space(self) -> Optional[str]:
        return self._knowledge_space

    @property
    def default_intent(self) -> QueryIntent:
        return self._intent

    @property
    def default_top_k(self) -> int:
        return self._top_k

    def _build_query(self, user_query: str, **kwargs: Any) -> KnowledgeQuery:
        return KnowledgeQuery(
            query=user_query,
            intent=self._intent,
            knowledge_space=self._knowledge_space,
            domains=[self._domain] if self._domain else [],
            top_k=self._top_k,
            include_graph_context=self._include_graph_context,
            agent_id=self._agent_id,
        )

    def _get_engine(self) -> Any:
        return self._engine


class ToolRegistry:
    """
    Registry of all knowledge tools available to agents.
    Agents discover available tools from this registry.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseKnowledgeTool] = {}

    def register(self, tool: BaseKnowledgeTool) -> None:
        self._tools[tool.tool_name] = tool
        logger.info("Registered knowledge tool: '%s'", tool.tool_name)

    def get(self, name: str) -> Optional[BaseKnowledgeTool]:
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, str]]:
        """Return tool manifest for agent tool discovery."""
        return [
            {"name": t.tool_name, "description": t.tool_description}
            for t in self._tools.values()
        ]

    def run(self, tool_name: str, query: str, **kwargs: Any) -> str:
        """Execute a tool by name."""
        tool = self.get(tool_name)
        if not tool:
            return f"[CIVITAS] Tool '{tool_name}' not found. Available: {list(self._tools.keys())}"
        return tool.run(query, **kwargs)

    def build_default_tools(
        self,
        engine: Any,
        agent_id: Optional[str] = None,
    ) -> "ToolRegistry":
        """
        Build and register the standard set of domain tools.
        Call this to provision a default tool set for an agent.
        """
        default_tools = [
            DomainSearchTool(
                engine=engine,
                name="search_legal_knowledge",
                description="Search legal documents: contracts, NDAs, compliance policies, litigation records.",
                knowledge_space="legal-contracts",
                domain="legal",
                intent=QueryIntent.FACTUAL,
                include_graph_context=True,
                agent_id=agent_id,
            ),
            DomainSearchTool(
                engine=engine,
                name="search_financial_knowledge",
                description="Search financial reports, invoices, budgets, and audit documents.",
                knowledge_space="finance-reports",
                domain="finance",
                intent=QueryIntent.FACTUAL,
                agent_id=agent_id,
            ),
            DomainSearchTool(
                engine=engine,
                name="search_hr_policies",
                description="Search HR policies, procedures, training materials, and recruitment documents.",
                knowledge_space="hr-policies",
                domain="hr",
                intent=QueryIntent.REGULATORY,
                agent_id=agent_id,
            ),
            DomainSearchTool(
                engine=engine,
                name="search_technical_docs",
                description="Search technical documentation, architecture records, runbooks, and API specs.",
                knowledge_space="technical-docs",
                domain="technical",
                intent=QueryIntent.PROCEDURAL,
                agent_id=agent_id,
            ),
            DomainSearchTool(
                engine=engine,
                name="search_global_knowledge",
                description="Search across all shared knowledge. Use when domain is unknown.",
                knowledge_space="global-shared",
                domain=None,
                intent=QueryIntent.EXPLORATION,
                top_k=10,
                agent_id=agent_id,
            ),
        ]
        for tool in default_tools:
            self.register(tool)
        return self

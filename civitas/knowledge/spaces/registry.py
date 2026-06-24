"""
civitas.knowledge.spaces.registry
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KnowledgeSpaceRegistry — manages all knowledge spaces.

The registry is the central authority for:
  · Creating and configuring knowledge spaces
  · Resolving spaces by name or ID
  · Access control checks at space level
  · Providing space configs to the indexing and retrieval layers
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from civitas.core.exceptions.knowledge_exceptions import (
    KnowledgeSpaceNotFoundError,
    KnowledgeSpaceNotActiveError,
)
from civitas.core.models.knowledge_space import (
    IndexStrategy,
    KnowledgeSpace,
    KnowledgeSpaceConfig,
    KnowledgeSpaceStatus,
    KnowledgeSpaceType,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  DEFAULT SPACES SEED
# ─────────────────────────────────────────────────────────────

def _build_default_spaces() -> list[KnowledgeSpace]:
    """Seed the platform with a standard set of knowledge spaces."""

    def _space(
        name: str,
        display_name: str,
        domain: str,
        space_type: KnowledgeSpaceType,
        owner_id: str,
        read_teams: list[str],
        write_teams: list[str],
        index_strategy: IndexStrategy = IndexStrategy.HYBRID,
        graphrag: bool = False,
    ) -> KnowledgeSpace:
        from civitas.core.models.knowledge_space import KnowledgeSpaceStatus
        sp = KnowledgeSpace(
            name=name,
            display_name=display_name,
            slug=name,
            domain=domain,
            space_type=space_type,
            owner_id=owner_id,
            owner_team_id=owner_id,
            read_teams=read_teams,
            write_teams=write_teams,
            status=KnowledgeSpaceStatus.ACTIVE,
            config=KnowledgeSpaceConfig(
                default_index_strategy=index_strategy,
                graph_rag_enabled=graphrag,
            ),
        )
        return sp

    return [
        _space(
            "global-shared", "Global Shared Knowledge", "global",
            KnowledgeSpaceType.GLOBAL, "system",
            read_teams=["all"], write_teams=["admin"],
            index_strategy=IndexStrategy.HYBRID,
        ),
        _space(
            "legal-contracts", "Legal — Contracts", "legal",
            KnowledgeSpaceType.DOMAIN, "legal-team",
            read_teams=["legal-team", "executive"], write_teams=["legal-team"],
            index_strategy=IndexStrategy.GRAPH_ENHANCED, graphrag=True,
        ),
        _space(
            "finance-reports", "Finance — Reports", "finance",
            KnowledgeSpaceType.DOMAIN, "finance-team",
            read_teams=["finance-team", "executive"], write_teams=["finance-team"],
            index_strategy=IndexStrategy.HYBRID,
        ),
        _space(
            "hr-policies", "HR — Policies & Procedures", "hr",
            KnowledgeSpaceType.DOMAIN, "hr-team",
            read_teams=["all"], write_teams=["hr-team"],
            index_strategy=IndexStrategy.HYBRID,
        ),
        _space(
            "technical-docs", "Technical Documentation", "technical",
            KnowledgeSpaceType.DOMAIN, "engineering",
            read_teams=["engineering", "product"], write_teams=["engineering"],
            index_strategy=IndexStrategy.FULL,
        ),
        _space(
            "external-knowledge", "External Knowledge Base", "external",
            KnowledgeSpaceType.EXTERNAL, "system",
            read_teams=["all"], write_teams=["admin"],
            index_strategy=IndexStrategy.SEMANTIC,
        ),
    ]


# ─────────────────────────────────────────────────────────────
#  REGISTRY
# ─────────────────────────────────────────────────────────────

class KnowledgeSpaceRegistry:
    """
    In-memory registry of all KnowledgeSpace objects.

    In production, back this with a PostgreSQL table and
    load on startup via KnowledgeSpaceRepository.load_all().
    """

    def __init__(self, spaces: Optional[list[KnowledgeSpace]] = None) -> None:
        self._by_id: dict[UUID, KnowledgeSpace] = {}
        self._by_name: dict[str, KnowledgeSpace] = {}
        seed = spaces if spaces is not None else _build_default_spaces()
        for sp in seed:
            self._register(sp)
        logger.info("KnowledgeSpaceRegistry: %d spaces loaded", len(self._by_id))

    def _register(self, space: KnowledgeSpace) -> None:
        self._by_id[space.id] = space
        self._by_name[space.name] = space

    # ── Lookup ─────────────────────────────────────────────────

    def get_by_name(self, name: str) -> KnowledgeSpace:
        space = self._by_name.get(name)
        if not space:
            raise KnowledgeSpaceNotFoundError(name)
        return space

    def get_by_id(self, space_id: UUID) -> KnowledgeSpace:
        space = self._by_id.get(space_id)
        if not space:
            raise KnowledgeSpaceNotFoundError(str(space_id))
        return space

    def get_active(self, name: str) -> KnowledgeSpace:
        """Get a space and assert it is active."""
        space = self.get_by_name(name)
        if not space.is_active:
            raise KnowledgeSpaceNotActiveError(name)
        return space

    def list_all(self, include_inactive: bool = False) -> list[KnowledgeSpace]:
        spaces = list(self._by_id.values())
        if not include_inactive:
            spaces = [s for s in spaces if s.is_active]
        return sorted(spaces, key=lambda s: s.name)

    def list_for_team(self, team_id: str) -> list[KnowledgeSpace]:
        """List all active spaces readable by a given team."""
        return [
            s for s in self.list_all()
            if team_id in s.read_teams or "all" in s.read_teams
        ]

    def list_for_agent(self, agent_id: str) -> list[KnowledgeSpace]:
        """List all active spaces accessible to a given agent."""
        return [
            s for s in self.list_all()
            if agent_id in s.read_agent_ids or "all" in s.read_teams
        ]

    # ── Mutation ───────────────────────────────────────────────

    def register(self, space: KnowledgeSpace) -> None:
        self._register(space)
        logger.info("Registered knowledge space: '%s'", space.name)

    def update(self, space: KnowledgeSpace) -> None:
        self._by_id[space.id] = space
        self._by_name[space.name] = space
        space.touch()

    def deactivate(self, name: str) -> None:
        space = self.get_by_name(name)
        space.status = KnowledgeSpaceStatus.ARCHIVED
        space.touch()
        logger.info("Deactivated knowledge space: '%s'", name)

    def record_query(self, name: str) -> None:
        try:
            self.get_by_name(name).record_query()
        except KnowledgeSpaceNotFoundError:
            pass

    # ── Introspection ──────────────────────────────────────────

    def summary(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "display_name": s.display_name,
                "status": s.status.value,
                "type": s.space_type.value,
                "documents": s.document_count,
                "chunks": s.chunk_count,
                "strategy": s.config.default_index_strategy.value,
                "graphrag": s.config.graph_rag_enabled,
            }
            for s in self.list_all(include_inactive=True)
        ]

"""
civitas.governance.access.rbac
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Role-Based Access Control (RBAC) for the CIVITAS platform.

Three-layer access model:
  Layer 1 — KnowledgeSpace level:  can this principal read/write this space?
  Layer 2 — Document level:        does this document's access_control allow it?
  Layer 3 — ClassificationLevel:   does the principal's clearance match the doc?

Roles are hierarchical:
  platform_admin  > space_admin > space_writer > space_reader > agent

Principals:
  · User       — human operator (has roles, teams, clearance)
  · Agent      — LangGraph agent (has allowed_spaces, capabilities)
  · System     — internal pipeline (always allowed)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from civitas.core.models.metadata import AccessLevel, ClassificationLevel

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  ROLES
# ─────────────────────────────────────────────────────────────

class PlatformRole(str, Enum):
    """Platform-level roles (apply globally)."""
    PLATFORM_ADMIN = "platform_admin"     # Full access to everything
    SPACE_ADMIN = "space_admin"           # Admin a specific space
    SPACE_WRITER = "space_writer"         # Ingest into a space
    SPACE_READER = "space_reader"         # Read from a space
    AGENT = "agent"                       # Machine agent

    @property
    def clearance_level(self) -> int:
        """Numeric clearance level for comparison."""
        return {
            self.PLATFORM_ADMIN: 100,
            self.SPACE_ADMIN: 80,
            self.SPACE_WRITER: 60,
            self.SPACE_READER: 40,
            self.AGENT: 20,
        }[self]


CLASSIFICATION_CLEARANCE: dict[ClassificationLevel, int] = {
    ClassificationLevel.PUBLIC: 0,
    ClassificationLevel.INTERNAL: 40,
    ClassificationLevel.CONFIDENTIAL: 60,
    ClassificationLevel.RESTRICTED: 80,
    ClassificationLevel.TOP_SECRET: 100,
}


# ─────────────────────────────────────────────────────────────
#  PRINCIPALS
# ─────────────────────────────────────────────────────────────

@dataclass
class UserPrincipal:
    """Represents an authenticated human user."""
    user_id: str
    email: str
    teams: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    clearance_level: int = 40   # Default: SPACE_READER
    allowed_spaces: list[str] = field(default_factory=list)    # Empty = determined by team
    is_platform_admin: bool = False

    @property
    def principal_id(self) -> str:
        return self.user_id

    @property
    def principal_type(self) -> str:
        return "user"

    def has_role(self, role: str) -> bool:
        return role in self.roles or self.is_platform_admin

    def in_team(self, team_id: str) -> bool:
        return team_id in self.teams


@dataclass
class AgentPrincipal:
    """Represents a LangGraph agent principal."""
    agent_id: str
    agent_name: str
    allowed_spaces: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    max_classification_level: ClassificationLevel = ClassificationLevel.CONFIDENTIAL
    capabilities: list[str] = field(default_factory=list)
    is_active: bool = True

    @property
    def principal_id(self) -> str:
        return self.agent_id

    @property
    def principal_type(self) -> str:
        return "agent"

    @property
    def clearance_level(self) -> int:
        return CLASSIFICATION_CLEARANCE.get(self.max_classification_level, 0)

    def can_access_space(self, space_name: str) -> bool:
        return space_name in self.allowed_spaces or "global-shared" == space_name

    def can_access_domain(self, domain: str) -> bool:
        return not self.allowed_domains or domain in self.allowed_domains


@dataclass
class SystemPrincipal:
    """Represents the internal CIVITAS system (pipeline, indexer)."""
    component: str = "system"

    @property
    def principal_id(self) -> str:
        return f"system:{self.component}"

    @property
    def principal_type(self) -> str:
        return "system"

    @property
    def clearance_level(self) -> int:
        return 100   # System has full clearance


Principal = UserPrincipal | AgentPrincipal | SystemPrincipal


# ─────────────────────────────────────────────────────────────
#  ACCESS DECISION
# ─────────────────────────────────────────────────────────────

@dataclass
class AccessDecision:
    """Result of an RBAC check."""
    allowed: bool
    principal_id: str
    resource: str
    action: str
    reason: str
    checked_layers: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.allowed


# ─────────────────────────────────────────────────────────────
#  RBAC ENGINE
# ─────────────────────────────────────────────────────────────

class RBACEngine:
    """
    Three-layer RBAC enforcement engine.

    Usage:
        rbac = RBACEngine()
        decision = rbac.can_read_document(
            principal=agent,
            doc_metadata=document.metadata,
            space_name="legal-contracts",
        )
        if not decision:
            raise AccessDeniedError(...)
    """

    # ── Space-level checks ─────────────────────────────────────

    def can_read_space(
        self,
        principal: Principal,
        space_name: str,
        space_read_teams: list[str],
        space_read_roles: list[str],
        space_read_agents: list[str],
    ) -> AccessDecision:
        """Layer 1: Can principal read from this knowledge space?"""
        layers: list[str] = []

        # System always allowed
        if isinstance(principal, SystemPrincipal):
            return AccessDecision(True, principal.principal_id, space_name, "read",
                                  "System principal", ["system"])

        # Platform admin always allowed
        if isinstance(principal, UserPrincipal) and principal.is_platform_admin:
            return AccessDecision(True, principal.principal_id, space_name, "read",
                                  "Platform admin", ["platform_admin"])

        layers.append("space_acl")

        # Agent check
        if isinstance(principal, AgentPrincipal):
            if principal.can_access_space(space_name) or principal.agent_id in space_read_agents:
                return AccessDecision(True, principal.principal_id, space_name, "read",
                                      f"Agent {principal.agent_id} allowed for space", layers)
            return AccessDecision(False, principal.principal_id, space_name, "read",
                                  f"Agent not in allowed_spaces for '{space_name}'", layers)

        # User checks
        if isinstance(principal, UserPrincipal):
            if "all" in space_read_teams:
                return AccessDecision(True, principal.principal_id, space_name, "read",
                                      "Space open to all users", layers)
            for team in principal.teams:
                if team in space_read_teams:
                    return AccessDecision(True, principal.principal_id, space_name, "read",
                                          f"Team '{team}' has read access", layers)
            for role in principal.roles:
                if role in space_read_roles:
                    return AccessDecision(True, principal.principal_id, space_name, "read",
                                          f"Role '{role}' has read access", layers)

        return AccessDecision(False, principal.principal_id, space_name, "read",
                              f"No read permission for space '{space_name}'", layers)

    # ── Document-level checks ──────────────────────────────────

    def can_read_document(
        self,
        principal: Principal,
        classification_level: ClassificationLevel,
        access_level: AccessLevel,
        allowed_teams: list[str],
        allowed_roles: list[str],
        allowed_agents: list[str],
        allowed_users: list[str],
    ) -> AccessDecision:
        """Layer 2+3: Can principal read this specific document?"""
        layers: list[str] = []

        # System always allowed
        if isinstance(principal, SystemPrincipal):
            return AccessDecision(True, principal.principal_id, "document", "read",
                                  "System principal", ["system"])

        # Layer 3: Classification clearance check
        layers.append("classification_check")
        required_clearance = CLASSIFICATION_CLEARANCE.get(classification_level, 0)
        if principal.clearance_level < required_clearance:
            return AccessDecision(
                False, principal.principal_id, "document", "read",
                f"Insufficient clearance: {principal.clearance_level} < {required_clearance} "
                f"(required for {classification_level.value})",
                layers,
            )

        # Layer 2: Access level check
        layers.append("access_level_check")

        if access_level in (AccessLevel.OPEN, AccessLevel.SPACE):
            return AccessDecision(True, principal.principal_id, "document", "read",
                                  f"Document access_level={access_level.value}", layers)

        if isinstance(principal, AgentPrincipal):
            if principal.agent_id in allowed_agents:
                return AccessDecision(True, principal.principal_id, "document", "read",
                                      "Agent ID in allowed_agent_ids", layers)
            if access_level == AccessLevel.AGENT:
                return AccessDecision(False, principal.principal_id, "document", "read",
                                      "Agent not in allowed_agent_ids", layers)

        if isinstance(principal, UserPrincipal):
            if principal.user_id in allowed_users:
                return AccessDecision(True, principal.principal_id, "document", "read",
                                      "User ID in allowed_user_ids", layers)
            for team in principal.teams:
                if team in allowed_teams:
                    return AccessDecision(True, principal.principal_id, "document", "read",
                                          f"Team '{team}' in allowed_teams", layers)
            for role in principal.roles:
                if role in allowed_roles:
                    return AccessDecision(True, principal.principal_id, "document", "read",
                                          f"Role '{role}' in allowed_roles", layers)

        return AccessDecision(False, principal.principal_id, "document", "read",
                              f"No permission for document (access_level={access_level.value})",
                              layers)

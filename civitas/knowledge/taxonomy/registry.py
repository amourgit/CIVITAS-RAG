"""
civitas.knowledge.taxonomy.registry
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TaxonomyRegistry — manages the global taxonomy tree.

The registry is the single authority on valid taxonomy paths.
It is loaded once at startup and held in memory.
All document classification decisions go through the registry.

Default taxonomy seed (extensible via admin API / database):
  legal / finance / hr / technical / commercial / external
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from civitas.core.models.taxonomy import TaxonomyNode, TaxonomyTree

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  DEFAULT TAXONOMY SEED
# ─────────────────────────────────────────────────────────────

def _build_default_taxonomy() -> list[TaxonomyNode]:
    """
    Seed the taxonomy with a standard enterprise knowledge structure.
    This is the initial default — extend via the admin registry API.
    """
    nodes: list[TaxonomyNode] = []

    def _node(
        name: str,
        display: str,
        path: list[str],
        parent_id: Optional[UUID],
        is_leaf: bool = False,
        keywords: Optional[list[str]] = None,
    ) -> TaxonomyNode:
        n = TaxonomyNode(
            name=name,
            display_name=display,
            path=path,
            parent_id=parent_id,
            depth=len(path) - 1,
            is_leaf=is_leaf,
            keywords=keywords or [],
        )
        nodes.append(n)
        return n

    # ── Legal ──────────────────────────────────────────────────
    legal = _node("legal", "Legal", ["legal"], None)
    contracts = _node("contracts", "Contracts", ["legal", "contracts"], legal.id)
    _node("service_agreements", "Service Agreements",
          ["legal", "contracts", "service_agreements"], contracts.id, is_leaf=True,
          keywords=["sla", "service", "agreement", "contract"])
    _node("nda", "Non-Disclosure Agreements",
          ["legal", "contracts", "nda"], contracts.id, is_leaf=True,
          keywords=["nda", "confidential", "disclosure", "non-disclosure"])
    _node("employment", "Employment Contracts",
          ["legal", "contracts", "employment"], contracts.id, is_leaf=True,
          keywords=["employment", "salary", "contract", "offer letter"])
    _node("partnership", "Partnership Agreements",
          ["legal", "contracts", "partnership"], contracts.id, is_leaf=True,
          keywords=["partnership", "joint venture", "mou"])

    litigation = _node("litigation", "Litigation", ["legal", "litigation"], legal.id)
    _node("pleadings", "Pleadings", ["legal", "litigation", "pleadings"], litigation.id, is_leaf=True)
    _node("court_orders", "Court Orders", ["legal", "litigation", "court_orders"], litigation.id, is_leaf=True)

    compliance = _node("compliance", "Compliance", ["legal", "compliance"], legal.id)
    _node("gdpr", "GDPR Documentation",
          ["legal", "compliance", "gdpr"], compliance.id, is_leaf=True,
          keywords=["gdpr", "privacy", "data protection"])
    _node("regulatory", "Regulatory Filings",
          ["legal", "compliance", "regulatory"], compliance.id, is_leaf=True)

    # ── Finance ────────────────────────────────────────────────
    finance = _node("finance", "Finance", ["finance"], None)
    reports = _node("reports", "Reports", ["finance", "reports"], finance.id)
    _node("quarterly", "Quarterly Reports",
          ["finance", "reports", "quarterly"], reports.id, is_leaf=True,
          keywords=["quarterly", "q1", "q2", "q3", "q4", "earnings"])
    _node("annual", "Annual Reports",
          ["finance", "reports", "annual"], reports.id, is_leaf=True,
          keywords=["annual", "yearly", "fiscal year"])
    invoices = _node("invoices", "Invoices", ["finance", "invoices"], finance.id)
    _node("supplier", "Supplier Invoices",
          ["finance", "invoices", "supplier"], invoices.id, is_leaf=True,
          keywords=["invoice", "supplier", "vendor", "payment"])
    _node("client", "Client Invoices",
          ["finance", "invoices", "client"], invoices.id, is_leaf=True)
    _node("budgets", "Budgets",
          ["finance", "budgets"], finance.id, is_leaf=True,
          keywords=["budget", "forecast", "planning", "allocation"])
    _node("audit", "Audit Reports",
          ["finance", "audit"], finance.id, is_leaf=True,
          keywords=["audit", "review", "internal control"])

    # ── HR ─────────────────────────────────────────────────────
    hr = _node("hr", "Human Resources", ["hr"], None)
    policies = _node("policies", "Policies", ["hr", "policies"], hr.id)
    _node("leave", "Leave Policy",
          ["hr", "policies", "leave"], policies.id, is_leaf=True,
          keywords=["leave", "vacation", "pto", "sick day"])
    _node("code_of_conduct", "Code of Conduct",
          ["hr", "policies", "code_of_conduct"], policies.id, is_leaf=True)
    _node("remote_work", "Remote Work Policy",
          ["hr", "policies", "remote_work"], policies.id, is_leaf=True)
    _node("recruitment", "Recruitment",
          ["hr", "recruitment"], hr.id, is_leaf=True,
          keywords=["recruitment", "hiring", "job description", "interview"])
    training = _node("training", "Training", ["hr", "training"], hr.id)
    _node("onboarding", "Onboarding",
          ["hr", "training", "onboarding"], training.id, is_leaf=True)
    _node("technical_training", "Technical Training",
          ["hr", "training", "technical_training"], training.id, is_leaf=True)

    # ── Technical ──────────────────────────────────────────────
    technical = _node("technical", "Technical", ["technical"], None)
    arch = _node("architecture", "Architecture", ["technical", "architecture"], technical.id)
    _node("adr", "Architecture Decision Records",
          ["technical", "architecture", "adr"], arch.id, is_leaf=True,
          keywords=["adr", "architecture decision", "design decision"])
    _node("design_docs", "Design Documents",
          ["technical", "architecture", "design_docs"], arch.id, is_leaf=True)
    _node("runbooks", "Runbooks",
          ["technical", "runbooks"], technical.id, is_leaf=True,
          keywords=["runbook", "operations", "incident", "procedure"])
    _node("api_docs", "API Documentation",
          ["technical", "api_docs"], technical.id, is_leaf=True,
          keywords=["api", "rest", "graphql", "endpoint", "swagger", "openapi"])
    _node("security", "Security Documentation",
          ["technical", "security"], technical.id, is_leaf=True,
          keywords=["security", "vulnerability", "cve", "pen test"])

    # ── Commercial ─────────────────────────────────────────────
    commercial = _node("commercial", "Commercial", ["commercial"], None)
    _node("proposals", "Proposals",
          ["commercial", "proposals"], commercial.id, is_leaf=True,
          keywords=["proposal", "rfp", "tender", "bid"])
    _node("case_studies", "Case Studies",
          ["commercial", "case_studies"], commercial.id, is_leaf=True)
    _node("pricing", "Pricing",
          ["commercial", "pricing"], commercial.id, is_leaf=True,
          keywords=["pricing", "rate card", "tariff", "quote"])

    # ── External ───────────────────────────────────────────────
    external = _node("external", "External Knowledge", ["external"], None)
    _node("market_research", "Market Research",
          ["external", "market_research"], external.id, is_leaf=True)
    _node("industry_reports", "Industry Reports",
          ["external", "industry_reports"], external.id, is_leaf=True)
    _node("news", "News & Press",
          ["external", "news"], external.id, is_leaf=True)

    return nodes


# ─────────────────────────────────────────────────────────────
#  REGISTRY
# ─────────────────────────────────────────────────────────────

class TaxonomyRegistry:
    """
    Global taxonomy registry.

    Singleton-ish: one instance per application, loaded at startup.
    Wraps TaxonomyTree with CRUD operations for admin management.
    """

    _instance: Optional["TaxonomyRegistry"] = None

    def __init__(self, nodes: Optional[list[TaxonomyNode]] = None) -> None:
        seed = nodes if nodes is not None else _build_default_taxonomy()
        self._nodes: dict[UUID, TaxonomyNode] = {n.id: n for n in seed}
        self._tree = TaxonomyTree(seed)
        logger.info(
            "TaxonomyRegistry initialized: %d nodes, %d leaf nodes",
            len(seed),
            len(self._tree.get_leaf_nodes()),
        )

    @classmethod
    def get_instance(cls) -> "TaxonomyRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Lookup ─────────────────────────────────────────────────

    def get_by_path(self, path: str) -> Optional[TaxonomyNode]:
        return self._tree.get_by_path(path)

    def get_by_id(self, node_id: UUID) -> Optional[TaxonomyNode]:
        return self._tree.get_by_id(node_id)

    def get_leaf_nodes(self) -> list[TaxonomyNode]:
        return self._tree.get_leaf_nodes()

    def get_children(self, path: str) -> list[TaxonomyNode]:
        node = self._tree.get_by_path(path)
        return self._tree.get_children(node.id) if node else []

    def validate_path(self, path_parts: list[str]) -> bool:
        return self._tree.validate_path(path_parts)

    # ── Mutation (admin operations) ────────────────────────────

    def add_node(self, node: TaxonomyNode) -> None:
        self._nodes[node.id] = node
        self._tree = TaxonomyTree(list(self._nodes.values()))
        logger.info("Added taxonomy node: %s", node.full_path)

    def deactivate_node(self, node_id: UUID) -> bool:
        node = self._nodes.get(node_id)
        if not node:
            return False
        node.is_active = False
        node.touch()
        self._tree = TaxonomyTree(list(self._nodes.values()))
        logger.info("Deactivated taxonomy node: %s", node.full_path)
        return True

    # ── Introspection ──────────────────────────────────────────

    def dump_tree(self, root_path: Optional[str] = None) -> list[dict]:
        """Return a list of all (or subtree) nodes as dicts for inspection."""
        if root_path:
            paths = self._tree.get_subtree_paths(root_path)
            nodes = [self._tree.get_by_path(p) for p in paths if self._tree.get_by_path(p)]
        else:
            nodes = list(self._nodes.values())
        return [
            {
                "path": n.full_path,
                "display_name": n.display_name,
                "depth": n.depth,
                "is_leaf": n.is_leaf,
                "is_active": n.is_active,
                "document_count": n.document_count,
            }
            for n in sorted(nodes, key=lambda x: x.full_path)
        ]

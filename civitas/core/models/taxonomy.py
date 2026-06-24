"""
civitas.core.models.taxonomy
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Taxonomic classification system for the CIVITAS knowledge platform.

The taxonomy provides a hierarchical, controlled vocabulary for
organizing all knowledge in the system. Every document must be
classified to a leaf node in the taxonomy.

Structure example:
  legal/
    contracts/
      service_agreements/
      nda/
      employment/
    litigation/
    compliance/
  finance/
    reports/
      quarterly/
      annual/
    invoices/
    budgets/
  hr/
    policies/
    recruitment/
    training/
  technical/
    architecture/
    runbooks/
    api_docs/

The taxonomy is:
  · Managed by administrators
  · Versioned (changes are tracked)
  · Used for metadata filtering during retrieval
  · Used for access control policies
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, field_validator


class TaxonomyNode(BaseModel):
    """
    A single node in the knowledge taxonomy tree.

    Each node represents a category at a specific level.
    Leaf nodes are the final classification targets for documents.
    """

    model_config = {"frozen": False}

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=200,
                      description="Machine-readable name (lowercase, underscores)")
    display_name: str = Field(..., description="Human-readable display name")
    description: Optional[str] = Field(None, max_length=1000)

    # ── Hierarchy ──────────────────────────────────────────────
    parent_id: Optional[UUID] = Field(None, description="Parent node ID (None = root)")
    path: list[str] = Field(
        default_factory=list,
        description="Full path from root to this node, e.g. ['legal', 'contracts', 'nda']",
    )
    depth: int = Field(default=0, ge=0, description="Depth in the tree (0 = root)")
    is_leaf: bool = Field(default=False, description="True if this node can receive documents")

    # ── Classification hints ───────────────────────────────────
    keywords: list[str] = Field(default_factory=list, description="Classification hint keywords")
    examples: list[str] = Field(default_factory=list, description="Example document types")

    # ── Governance ─────────────────────────────────────────────
    is_active: bool = Field(default=True)
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Statistics ─────────────────────────────────────────────
    document_count: int = Field(default=0, ge=0)

    # ── Custom ─────────────────────────────────────────────────
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def full_path(self) -> str:
        """Dot-separated full path: 'legal.contracts.nda'"""
        return ".".join(self.path)

    @computed_field
    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Names must be lowercase, no spaces."""
        return v.lower().replace(" ", "_").replace("-", "_")

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: list[str]) -> list[str]:
        """All path segments must be lowercase."""
        return [p.lower().replace(" ", "_") for p in v]

    def get_child_path(self, child_name: str) -> list[str]:
        """Build path for a child node."""
        return self.path + [child_name.lower().replace(" ", "_")]

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()

    def __repr__(self) -> str:
        return f"TaxonomyNode(path='{self.full_path}', leaf={self.is_leaf})"


class TaxonomyTree:
    """
    In-memory representation of the full taxonomy.
    Built from a flat list of TaxonomyNodes.
    Supports path lookup, child enumeration, and validation.
    """

    def __init__(self, nodes: list[TaxonomyNode]) -> None:
        self._nodes: dict[UUID, TaxonomyNode] = {n.id: n for n in nodes}
        self._path_index: dict[str, UUID] = {n.full_path: n.id for n in nodes}
        self._children: dict[UUID, list[UUID]] = {}
        self._build_children_index()

    def _build_children_index(self) -> None:
        for node in self._nodes.values():
            if node.parent_id:
                self._children.setdefault(node.parent_id, []).append(node.id)

    def get_by_path(self, path: str) -> Optional[TaxonomyNode]:
        """Look up a node by its dot-separated path."""
        node_id = self._path_index.get(path)
        return self._nodes.get(node_id) if node_id else None

    def get_by_id(self, node_id: UUID) -> Optional[TaxonomyNode]:
        return self._nodes.get(node_id)

    def get_children(self, node_id: UUID) -> list[TaxonomyNode]:
        """Return all direct children of a node."""
        return [self._nodes[cid] for cid in self._children.get(node_id, [])]

    def get_leaf_nodes(self) -> list[TaxonomyNode]:
        """Return all leaf nodes (valid classification targets)."""
        return [n for n in self._nodes.values() if n.is_leaf and n.is_active]

    def get_ancestors(self, node_id: UUID) -> list[TaxonomyNode]:
        """Return all ancestor nodes from root to the given node."""
        node = self._nodes.get(node_id)
        if not node:
            return []
        ancestors = []
        for path_segment in node.path[:-1]:
            # Build partial path for lookup
            partial = ".".join(node.path[:node.path.index(path_segment) + 1])
            ancestor = self.get_by_path(partial)
            if ancestor:
                ancestors.append(ancestor)
        return ancestors

    def get_subtree_paths(self, root_path: str) -> list[str]:
        """Return all paths in the subtree rooted at root_path."""
        return [p for p in self._path_index if p == root_path or p.startswith(root_path + ".")]

    def validate_path(self, path: list[str]) -> bool:
        """Check if a taxonomy path corresponds to a valid leaf node."""
        full = ".".join(path)
        node = self.get_by_path(full)
        return node is not None and node.is_leaf and node.is_active

    @property
    def total_nodes(self) -> int:
        return len(self._nodes)

    @property
    def root_nodes(self) -> list[TaxonomyNode]:
        return [n for n in self._nodes.values() if n.is_root]

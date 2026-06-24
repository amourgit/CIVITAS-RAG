"""
tests/unit/test_taxonomy.py  —  Taxonomy registry and lifecycle tests
"""

from __future__ import annotations

import pytest

from civitas.knowledge.taxonomy.registry import TaxonomyRegistry
from civitas.knowledge.spaces.registry import KnowledgeSpaceRegistry
from civitas.governance.lifecycle.states import (
    ALLOWED_TRANSITIONS,
    can_retrieve,
    is_transition_allowed,
)
from civitas.governance.lifecycle.manager import LifecycleManager
from civitas.governance.quality.checker import (
    Disposition,
    MinimumWordCountRule,
    NoGarbageContentRule,
    HasRequiredMetadataRule,
    QualityChecker,
)
from civitas.core.models.metadata import DocumentLifecycleState as State


# ─────────────────────────────────────────────────────────────
#  TAXONOMY REGISTRY
# ─────────────────────────────────────────────────────────────

class TestTaxonomyRegistry:
    def test_default_registry_loads(self):
        registry = TaxonomyRegistry()
        assert registry._tree.total_nodes > 0

    def test_default_registry_has_leaf_nodes(self):
        registry = TaxonomyRegistry()
        leaves = registry.get_leaf_nodes()
        assert len(leaves) > 0

    def test_lookup_by_path_legal(self):
        registry = TaxonomyRegistry()
        node = registry.get_by_path("legal")
        assert node is not None
        assert node.name == "legal"

    def test_lookup_leaf_service_agreements(self):
        registry = TaxonomyRegistry()
        node = registry.get_by_path("legal.contracts.service_agreements")
        assert node is not None
        assert node.is_leaf is True

    def test_validate_leaf_path_valid(self):
        registry = TaxonomyRegistry()
        assert registry.validate_path(["legal", "contracts", "nda"]) is True

    def test_validate_non_leaf_path(self):
        registry = TaxonomyRegistry()
        assert registry.validate_path(["legal", "contracts"]) is False

    def test_validate_nonexistent_path(self):
        registry = TaxonomyRegistry()
        assert registry.validate_path(["nonexistent", "path"]) is False

    def test_get_children_of_legal(self):
        registry = TaxonomyRegistry()
        children = registry.get_children("legal")
        assert len(children) > 0
        child_names = [c.name for c in children]
        assert "contracts" in child_names

    def test_dump_tree_returns_list(self):
        registry = TaxonomyRegistry()
        tree_dump = registry.dump_tree()
        assert isinstance(tree_dump, list)
        assert len(tree_dump) > 0
        first = tree_dump[0]
        assert "path" in first
        assert "display_name" in first
        assert "is_leaf" in first

    def test_dump_tree_subtree(self):
        registry = TaxonomyRegistry()
        subtree = registry.dump_tree("legal")
        assert all(item["path"].startswith("legal") for item in subtree)

    def test_deactivate_node(self):
        registry = TaxonomyRegistry()
        node = registry.get_by_path("external.news")
        assert node is not None
        registry.deactivate_node(node.id)
        # After deactivation, leaf nodes should not include it
        leaves = registry.get_leaf_nodes()
        assert all(l.name != "news" for l in leaves)


# ─────────────────────────────────────────────────────────────
#  KNOWLEDGE SPACE REGISTRY
# ─────────────────────────────────────────────────────────────

class TestKnowledgeSpaceRegistry:
    def test_default_spaces_loaded(self):
        registry = KnowledgeSpaceRegistry()
        spaces = registry.list_all()
        assert len(spaces) > 0

    def test_get_by_name_legal(self):
        registry = KnowledgeSpaceRegistry()
        space = registry.get_by_name("legal-contracts")
        assert space is not None
        assert space.name == "legal-contracts"

    def test_get_by_name_not_found_raises(self):
        from civitas.core.exceptions.knowledge_exceptions import KnowledgeSpaceNotFoundError
        registry = KnowledgeSpaceRegistry()
        with pytest.raises(KnowledgeSpaceNotFoundError):
            registry.get_by_name("nonexistent-space")

    def test_list_for_team(self):
        registry = KnowledgeSpaceRegistry()
        spaces = registry.list_for_team("legal-team")
        names = [s.name for s in spaces]
        assert "legal-contracts" in names

    def test_all_team_spaces_accessible_to_all(self):
        registry = KnowledgeSpaceRegistry()
        spaces = registry.list_for_team("any-team")
        # global-shared and hr-policies have "all" in read_teams
        names = [s.name for s in spaces]
        assert "global-shared" in names

    def test_summary_returns_list(self):
        registry = KnowledgeSpaceRegistry()
        summary = registry.summary()
        assert isinstance(summary, list)
        assert all("name" in s for s in summary)


# ─────────────────────────────────────────────────────────────
#  LIFECYCLE STATES
# ─────────────────────────────────────────────────────────────

class TestLifecycleStates:
    def test_draft_can_go_to_pending_review(self):
        assert is_transition_allowed(State.DRAFT, State.PENDING_REVIEW) is True

    def test_draft_cannot_go_to_active_directly(self):
        assert is_transition_allowed(State.DRAFT, State.ACTIVE) is False

    def test_approved_can_go_to_active(self):
        assert is_transition_allowed(State.APPROVED, State.ACTIVE) is True

    def test_active_can_go_to_deprecated(self):
        assert is_transition_allowed(State.ACTIVE, State.DEPRECATED) is True

    def test_active_cannot_go_to_draft(self):
        assert is_transition_allowed(State.ACTIVE, State.DRAFT) is False

    def test_purged_is_terminal(self):
        assert ALLOWED_TRANSITIONS[State.PURGED] == set()

    def test_can_retrieve_active(self):
        assert can_retrieve(State.ACTIVE) is True

    def test_can_retrieve_approved(self):
        assert can_retrieve(State.APPROVED) is True

    def test_cannot_retrieve_draft(self):
        assert can_retrieve(State.DRAFT) is False

    def test_cannot_retrieve_deprecated(self):
        assert can_retrieve(State.DEPRECATED) is False


# ─────────────────────────────────────────────────────────────
#  LIFECYCLE MANAGER
# ─────────────────────────────────────────────────────────────

class TestLifecycleManager:
    def test_submit_for_review(self, sample_document):
        manager = LifecycleManager()
        result = manager.submit_for_review(sample_document, submitter="user-001")
        assert result.metadata.lifecycle_state == State.PENDING_REVIEW

    def test_approve(self, sample_document):
        manager = LifecycleManager()
        manager.submit_for_review(sample_document, submitter="user-001")
        # Simulate IN_REVIEW step manually
        sample_document.metadata.lifecycle_state = State.IN_REVIEW
        result = manager.approve(sample_document, approver="manager-001")
        assert result.metadata.lifecycle_state == State.APPROVED
        assert result.metadata.approved_by == "manager-001"
        assert result.metadata.approved_at is not None

    def test_invalid_transition_raises(self, sample_document):
        from civitas.core.exceptions.knowledge_exceptions import LifecycleTransitionError
        manager = LifecycleManager()
        # DRAFT → ACTIVE is not allowed directly
        with pytest.raises(LifecycleTransitionError):
            manager.transition(sample_document, State.ACTIVE, actor="user-001")

    def test_full_lifecycle_flow(self, sample_document):
        manager = LifecycleManager()
        manager.submit_for_review(sample_document, "submitter")
        sample_document.metadata.lifecycle_state = State.IN_REVIEW
        manager.approve(sample_document, "approver")
        manager.activate(sample_document, "system")
        assert sample_document.metadata.lifecycle_state == State.ACTIVE
        manager.deprecate(sample_document, "admin", "replaced by v2")
        assert sample_document.metadata.lifecycle_state == State.DEPRECATED
        manager.archive(sample_document, "admin")
        assert sample_document.metadata.lifecycle_state == State.ARCHIVED

    def test_can_retrieve_active_document(self, sample_document):
        manager = LifecycleManager()
        sample_document.metadata.lifecycle_state = State.ACTIVE
        assert manager.can_retrieve(sample_document) is True

    def test_cannot_retrieve_draft_document(self, sample_document):
        manager = LifecycleManager()
        assert manager.can_retrieve(sample_document) is False


# ─────────────────────────────────────────────────────────────
#  QUALITY CHECKER
# ─────────────────────────────────────────────────────────────

class TestQualityChecker:
    def test_minimum_word_count_pass(self, sample_document):
        rule = MinimumWordCountRule(min_words=10)
        result = rule.check(sample_document)
        assert result.passed is True
        assert result.score == 1.0

    def test_minimum_word_count_fail(self, sample_document):
        sample_document.set_content("Too short.")
        rule = MinimumWordCountRule(min_words=100)
        result = rule.check(sample_document)
        assert result.passed is False
        assert result.score < 1.0

    def test_garbage_content_clean(self, sample_document):
        rule = NoGarbageContentRule()
        result = rule.check(sample_document)
        assert result.passed is True

    def test_has_required_metadata_pass(self, sample_document):
        rule = HasRequiredMetadataRule()
        result = rule.check(sample_document)
        assert result.passed is True

    def test_has_required_metadata_fail_no_domain(self, sample_document):
        sample_document.metadata.domain = ""
        rule = HasRequiredMetadataRule()
        result = rule.check(sample_document)
        assert result.passed is False

    def test_quality_checker_accept(self, sample_document):
        checker = QualityChecker(accept_threshold=0.1, reject_threshold=0.0)
        report = checker.check(sample_document)
        # Good document should not be rejected
        assert report.disposition != Disposition.REJECT

    def test_quality_checker_reject_empty_content(self, sample_document):
        sample_document.content = ""
        sample_document.word_count = 0
        checker = QualityChecker(reject_threshold=0.9)
        report = checker.check(sample_document)
        assert report.disposition == Disposition.REJECT

    def test_apply_to_document_updates_metadata(self, sample_document):
        checker = QualityChecker()
        updated = checker.apply_to_document(sample_document)
        assert updated.metadata.quality.quality_checked_at is not None
        assert updated.metadata.quality.quality_score is not None

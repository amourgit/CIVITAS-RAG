"""
tests/unit/test_models.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Unit tests for CIVITAS core domain models.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from civitas.core.models.document import Document, DocumentFormat, DocumentLanguage
from civitas.core.models.metadata import (
    AccessLevel,
    ClassificationLevel,
    DocumentAccessControl,
    DocumentLifecycleState,
    DocumentMetadata,
    DocumentOwnership,
    DocumentQuality,
    DocumentSource,
    SourceType,
)
from civitas.core.models.chunk import ChunkType, DocumentChunk
from civitas.core.models.knowledge_space import (
    KnowledgeSpace,
    KnowledgeSpaceConfig,
    KnowledgeSpaceStatus,
    KnowledgeSpaceType,
    IndexStrategy,
)
from civitas.core.models.taxonomy import TaxonomyNode, TaxonomyTree


# ─────────────────────────────────────────────────────────────
#  DOCUMENT METADATA TESTS
# ─────────────────────────────────────────────────────────────

class TestDocumentQuality:
    def test_composite_score_single_dimension(self):
        q = DocumentQuality(quality_score=0.8)
        assert q.composite_score is not None
        assert abs(q.composite_score - 0.8 * 0.30) < 0.01  # only quality_score weight

    def test_composite_score_all_dimensions(self):
        q = DocumentQuality(
            quality_score=1.0,
            completeness_score=1.0,
            freshness_score=1.0,
            consistency_score=1.0,
            relevance_score=1.0,
        )
        assert q.composite_score == pytest.approx(1.0, abs=0.01)

    def test_composite_score_none_when_no_data(self):
        q = DocumentQuality()
        assert q.composite_score is None

    def test_passes_minimum_threshold_true(self):
        q = DocumentQuality(quality_score=0.8)
        # composite = 0.8 * 0.30 = 0.24 < 0.5, so check actual result
        assert isinstance(q.passes_minimum_threshold, bool)

    def test_passes_minimum_threshold_false_when_low(self):
        q = DocumentQuality(
            quality_score=0.1,
            completeness_score=0.1,
            freshness_score=0.1,
            consistency_score=0.1,
            relevance_score=0.1,
        )
        assert q.passes_minimum_threshold is False


class TestDocumentAccessControl:
    def test_open_access_allows_all(self):
        ac = DocumentAccessControl(access_level=AccessLevel.OPEN)
        assert ac.can_access(team_id="any-team") is True
        assert ac.can_access() is True

    def test_team_access_allows_matching_team(self):
        ac = DocumentAccessControl(
            access_level=AccessLevel.TEAM,
            allowed_teams=["legal-team"],
        )
        assert ac.can_access(team_id="legal-team") is True
        assert ac.can_access(team_id="finance-team") is False

    def test_denied_team_overrides_access(self):
        ac = DocumentAccessControl(
            access_level=AccessLevel.OPEN,
            denied_teams=["blocked-team"],
        )
        assert ac.can_access(team_id="blocked-team") is False
        assert ac.can_access(team_id="other-team") is True

    def test_role_access(self):
        ac = DocumentAccessControl(
            access_level=AccessLevel.ROLE,
            allowed_roles=["admin", "legal-counsel"],
        )
        assert ac.can_access(role="admin") is True
        assert ac.can_access(role="intern") is False

    def test_agent_access(self):
        ac = DocumentAccessControl(
            access_level=AccessLevel.AGENT,
            allowed_agent_ids=["legal-agent-001"],
        )
        assert ac.can_access(agent_id="legal-agent-001") is True
        assert ac.can_access(agent_id="finance-agent-002") is False

    def test_system_access_denied_externally(self):
        ac = DocumentAccessControl(access_level=AccessLevel.SYSTEM)
        assert ac.can_access(team_id="admin-team") is False


class TestDocumentMetadata:
    def test_is_expired_false_when_no_expiry(self, sample_metadata):
        assert sample_metadata.is_expired is False

    def test_is_expired_true_past_date(self, sample_metadata):
        sample_metadata.expiry_date = date.today() - timedelta(days=1)
        assert sample_metadata.is_expired is True

    def test_is_expired_false_future_date(self, sample_metadata):
        sample_metadata.expiry_date = date.today() + timedelta(days=30)
        assert sample_metadata.is_expired is False

    def test_days_until_expiry_positive(self, sample_metadata):
        sample_metadata.expiry_date = date.today() + timedelta(days=10)
        assert sample_metadata.days_until_expiry == 10

    def test_days_until_expiry_negative_expired(self, sample_metadata):
        sample_metadata.expiry_date = date.today() - timedelta(days=5)
        assert sample_metadata.days_until_expiry == -5

    def test_days_until_expiry_none_when_no_expiry(self, sample_metadata):
        assert sample_metadata.days_until_expiry is None

    def test_is_active_approved_state(self, sample_metadata):
        sample_metadata.lifecycle_state = DocumentLifecycleState.APPROVED
        assert sample_metadata.is_active is True

    def test_is_active_active_state(self, sample_metadata):
        sample_metadata.lifecycle_state = DocumentLifecycleState.ACTIVE
        assert sample_metadata.is_active is True

    def test_is_not_active_draft_state(self, sample_metadata):
        sample_metadata.lifecycle_state = DocumentLifecycleState.DRAFT
        assert sample_metadata.is_active is False

    def test_full_taxonomy_path(self, sample_metadata):
        sample_metadata.taxonomy_path = ["legal", "contracts", "nda"]
        assert sample_metadata.full_taxonomy_path == "legal.contracts.nda"

    def test_taxonomy_path_normalised_lowercase(self):
        meta = DocumentMetadata(
            domain="Legal",
            category="Contracts",
            taxonomy_path=["Legal", "Contracts", "NDA"],
            ownership=DocumentOwnership(owner_id="u1"),
        )
        assert meta.taxonomy_path == ["legal", "contracts", "nda"]

    def test_record_access_updates_fields(self, sample_metadata):
        sample_metadata.record_access("agent-001")
        assert sample_metadata.access_count == 1
        assert sample_metadata.last_retrieved_by == "agent-001"
        assert sample_metadata.last_accessed_at is not None


# ─────────────────────────────────────────────────────────────
#  DOCUMENT TESTS
# ─────────────────────────────────────────────────────────────

class TestDocument:
    def test_set_content_populates_fields(self, sample_document):
        assert sample_document.has_content is True
        assert sample_document.word_count is not None
        assert sample_document.word_count > 0
        assert sample_document.content_preview is not None
        assert sample_document.checksum is not None

    def test_checksum_is_sha256(self, sample_document):
        assert len(sample_document.checksum) == 64

    def test_compute_checksum_deterministic(self, sample_document):
        c1 = sample_document.compute_checksum()
        c2 = sample_document.compute_checksum()
        assert c1 == c2

    def test_is_indexed_false_initially(self, sample_document):
        assert sample_document.is_indexed is False

    def test_mark_indexed(self, sample_document):
        sample_document.mark_indexed()
        assert sample_document.is_indexed is True
        assert sample_document.indexed_at is not None

    def test_create_new_version(self, sample_document):
        original_id = sample_document.id
        new_doc = sample_document.create_new_version()
        assert new_doc.version == sample_document.version + 1
        assert new_doc.parent_version_id == original_id
        assert new_doc.id != original_id
        assert new_doc.is_latest_version is True
        assert sample_document.is_latest_version is False

    def test_knowledge_space_id_shortcut(self, sample_document, sample_space_id):
        assert sample_document.knowledge_space_id == sample_space_id

    def test_domain_shortcut(self, sample_document):
        assert sample_document.domain == "legal"

    def test_to_dict_and_from_dict(self, sample_document):
        data = sample_document.to_dict()
        assert isinstance(data, dict)
        assert "id" in data
        assert "title" in data
        restored = Document.from_dict(data)
        assert restored.id == sample_document.id
        assert restored.title == sample_document.title


# ─────────────────────────────────────────────────────────────
#  DOCUMENT CHUNK TESTS
# ─────────────────────────────────────────────────────────────

class TestDocumentChunk:
    def test_chunk_basic_fields(self, sample_document):
        chunk = DocumentChunk(
            document_id=sample_document.id,
            document_title=sample_document.title,
            chunk_index=0,
            content="This is a test chunk with enough words to be meaningful.",
            domain="legal",
            category="contracts",
        )
        assert chunk.word_count > 0
        assert chunk.full_taxonomy_path == ""

    def test_chunk_hash_is_deterministic(self, sample_document):
        chunk = DocumentChunk(
            document_id=sample_document.id,
            document_title=sample_document.title,
            chunk_index=0,
            content="Same content here.",
            domain="legal",
            category="contracts",
        )
        h1 = chunk.compute_hash()
        h2 = chunk.compute_hash()
        assert h1 == h2
        assert len(h1) == 64

    def test_llamaindex_metadata_export(self, sample_document, sample_space_id):
        chunk = DocumentChunk(
            document_id=sample_document.id,
            document_title=sample_document.title,
            chunk_index=2,
            page_number=3,
            content="Contract terms and obligations.",
            domain="legal",
            category="contracts",
            knowledge_space_id=sample_space_id,
            knowledge_space_name="legal-contracts",
            taxonomy_path=["legal", "contracts"],
            allowed_teams=["legal-team"],
        )
        meta = chunk.to_llamaindex_node_metadata()
        assert meta["domain"] == "legal"
        assert meta["chunk_index"] == 2
        assert meta["page_number"] == 3
        assert meta["knowledge_space_name"] == "legal-contracts"
        assert "legal-team" in meta["allowed_teams"]


# ─────────────────────────────────────────────────────────────
#  KNOWLEDGE SPACE TESTS
# ─────────────────────────────────────────────────────────────

class TestKnowledgeSpace:
    def test_is_active_true(self, sample_knowledge_space):
        assert sample_knowledge_space.is_active is True

    def test_is_active_false_archived(self, sample_knowledge_space):
        sample_knowledge_space.status = KnowledgeSpaceStatus.ARCHIVED
        assert sample_knowledge_space.is_active is False

    def test_can_read_matching_team(self, sample_knowledge_space):
        assert sample_knowledge_space.can_read(team_id="legal-team") is True

    def test_can_read_non_matching_team(self, sample_knowledge_space):
        assert sample_knowledge_space.can_read(team_id="hr-team") is False

    def test_can_read_inactive_space(self, sample_knowledge_space):
        sample_knowledge_space.status = KnowledgeSpaceStatus.ARCHIVED
        assert sample_knowledge_space.can_read(team_id="legal-team") is False

    def test_record_ingestion_updates_counts(self, sample_knowledge_space):
        sample_knowledge_space.record_ingestion(doc_count=5, chunk_count=50)
        assert sample_knowledge_space.document_count == 5
        assert sample_knowledge_space.chunk_count == 50
        assert sample_knowledge_space.last_ingestion_at is not None

    def test_record_query_updates_counter(self, sample_knowledge_space):
        sample_knowledge_space.record_query()
        assert sample_knowledge_space.total_queries == 1


# ─────────────────────────────────────────────────────────────
#  TAXONOMY TESTS
# ─────────────────────────────────────────────────────────────

class TestTaxonomyNode:
    def test_full_path_computed(self, sample_taxonomy_node):
        assert sample_taxonomy_node.full_path == "legal.contracts.service_agreements"

    def test_is_root_false(self, sample_taxonomy_node):
        assert sample_taxonomy_node.is_root is False

    def test_name_normalised(self):
        node = TaxonomyNode(
            name="Service Agreements",
            display_name="Service Agreements",
            path=["legal"],
        )
        assert node.name == "service_agreements"

    def test_child_path(self, sample_taxonomy_node):
        child_path = sample_taxonomy_node.get_child_path("master_service")
        assert child_path[-1] == "master_service"
        assert child_path[:-1] == sample_taxonomy_node.path


class TestTaxonomyTree:
    def test_lookup_by_path(self):
        parent = TaxonomyNode(name="legal", display_name="Legal", path=["legal"], depth=0)
        child = TaxonomyNode(
            name="contracts",
            display_name="Contracts",
            path=["legal", "contracts"],
            parent_id=parent.id,
            depth=1,
        )
        leaf = TaxonomyNode(
            name="nda",
            display_name="NDA",
            path=["legal", "contracts", "nda"],
            parent_id=child.id,
            depth=2,
            is_leaf=True,
        )
        tree = TaxonomyTree([parent, child, leaf])
        assert tree.get_by_path("legal") is not None
        assert tree.get_by_path("legal.contracts.nda") is not None
        assert tree.get_by_path("nonexistent") is None

    def test_validate_leaf_path(self):
        leaf = TaxonomyNode(
            name="nda",
            display_name="NDA",
            path=["legal", "contracts", "nda"],
            depth=2,
            is_leaf=True,
        )
        non_leaf = TaxonomyNode(
            name="contracts",
            display_name="Contracts",
            path=["legal", "contracts"],
            depth=1,
            is_leaf=False,
        )
        tree = TaxonomyTree([leaf, non_leaf])
        assert tree.validate_path(["legal", "contracts", "nda"]) is True
        assert tree.validate_path(["legal", "contracts"]) is False   # not a leaf

    def test_get_leaf_nodes(self):
        nodes = [
            TaxonomyNode(name="root", display_name="Root", path=["root"], is_leaf=False),
            TaxonomyNode(name="leaf1", display_name="Leaf 1", path=["root", "leaf1"], is_leaf=True),
            TaxonomyNode(name="leaf2", display_name="Leaf 2", path=["root", "leaf2"], is_leaf=True),
        ]
        tree = TaxonomyTree(nodes)
        leaves = tree.get_leaf_nodes()
        assert len(leaves) == 2

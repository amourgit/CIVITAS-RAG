"""
tests/conftest.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Shared fixtures for the CIVITAS test suite.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

import pytest

from civitas.core.models.document import Document, DocumentFormat, DocumentLanguage
from civitas.core.models.knowledge_space import (
    IndexStrategy,
    KnowledgeSpace,
    KnowledgeSpaceConfig,
    KnowledgeSpaceStatus,
    KnowledgeSpaceType,
)
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
from civitas.core.models.taxonomy import TaxonomyNode


# ─────────────────────────────────────────────────────────────
#  METADATA FIXTURES
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_space_id() -> uuid.UUID:
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def sample_metadata(sample_space_id: uuid.UUID) -> DocumentMetadata:
    return DocumentMetadata(
        domain="legal",
        category="contracts",
        taxonomy_path=["legal", "contracts", "service_agreements"],
        knowledge_space_id=sample_space_id,
        knowledge_space_name="legal-contracts",
        ownership=DocumentOwnership(
            owner_id="user-001",
            team_id="legal-team",
            author="Jane Doe",
        ),
        source=DocumentSource(
            source_type=SourceType.FILESYSTEM,
            original_filename="service_agreement.pdf",
            source_path="/data/legal/service_agreement.pdf",
        ),
        access_control=DocumentAccessControl(
            classification_level=ClassificationLevel.CONFIDENTIAL,
            access_level=AccessLevel.TEAM,
            allowed_teams=["legal-team", "executive"],
        ),
    )


@pytest.fixture
def sample_document(sample_metadata: DocumentMetadata) -> Document:
    doc = Document(
        title="Service Level Agreement — ACME Corp 2024",
        format=DocumentFormat.PDF,
        language=DocumentLanguage.ENGLISH,
        byte_size=125_000,
        page_count=12,
        metadata=sample_metadata,
    )
    doc.set_content(
        "This Service Level Agreement ('Agreement') is entered into as of January 1, 2024, "
        "between ACME Corporation ('Client') and TechCo Inc ('Provider'). "
        "The Provider agrees to maintain 99.9% uptime for all production services. "
        "In the event of downtime exceeding 0.1% in any calendar month, "
        "the Client shall receive service credits as defined in Schedule A. "
        "All support requests must be acknowledged within 2 business hours. "
        "Critical incidents must be resolved within 4 hours. "
        "This agreement is governed by the laws of France. "
        "Any disputes shall be resolved through binding arbitration in Paris. "
        "Either party may terminate this agreement with 30 days written notice. "
        "The Provider shall maintain appropriate cybersecurity measures at all times. "
        "Data processed under this agreement is subject to GDPR compliance obligations."
    )
    return doc


@pytest.fixture
def sample_knowledge_space(sample_space_id: uuid.UUID) -> KnowledgeSpace:
    return KnowledgeSpace(
        id=sample_space_id,
        name="legal-contracts",
        display_name="Legal — Contracts",
        slug="legal-contracts",
        space_type=KnowledgeSpaceType.DOMAIN,
        domain="legal",
        status=KnowledgeSpaceStatus.ACTIVE,
        owner_id="legal-team",
        owner_team_id="legal-team",
        read_teams=["legal-team", "executive"],
        write_teams=["legal-team"],
        config=KnowledgeSpaceConfig(
            default_index_strategy=IndexStrategy.HYBRID,
            graph_rag_enabled=False,
        ),
    )


@pytest.fixture
def sample_taxonomy_node() -> TaxonomyNode:
    return TaxonomyNode(
        name="service_agreements",
        display_name="Service Agreements",
        path=["legal", "contracts", "service_agreements"],
        depth=2,
        is_leaf=True,
        keywords=["sla", "service", "agreement"],
    )

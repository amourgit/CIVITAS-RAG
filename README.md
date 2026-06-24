# CIVITAS — Enterprise Knowledge Platform

> **C**omprehensive **I**ntelligent **V**ector **I**ndex and **T**axonomy **A**ugmented **S**ystem

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![LlamaIndex](https://img.shields.io/badge/LlamaIndex-0.10+-green.svg)](https://www.llamaindex.ai/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-blue.svg)](https://www.postgresql.org/)
[![pgvector](https://img.shields.io/badge/pgvector-0.5+-orange.svg)](https://github.com/pgvector/pgvector)

---

## What is CIVITAS?

CIVITAS is an **enterprise-grade knowledge platform** built as the cognitive backbone of an AI-native organization. It transforms unstructured organizational documents — contracts, policies, reports, runbooks, research — into a **governed, structured, indexed knowledge ecosystem** that AI agents can query with precision and trust.

This is **not** a document storage system.  
This is **not** a simple search engine.  
This is a **knowledge infrastructure** designed to serve multiple AI agents over years of operation.

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CIVITAS Knowledge Platform                      │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Knowledge Access Layer (civitas.access)          │  │
│  │     UnifiedKnowledgeEngine  ·  ToolRegistry  ·  Contracts    │  │
│  └───────────────────────────┬──────────────────────────────────┘  │
│                              │ (sole agent entry point)            │
│  ┌───────────────────────────▼──────────────────────────────────┐  │
│  │                 Retrieval Engine (civitas.retrieval)          │  │
│  │   SemanticRetrieval  ·  KeywordRetrieval  ·  GraphRetrieval  │  │
│  │             RRF Fusion  ·  Cross-Encoder Reranking           │  │
│  └──────┬────────────────────┬────────────────────┬────────────┘  │
│         │                    │                    │               │
│  ┌──────▼──────┐  ┌──────────▼──────┐  ┌─────────▼──────┐       │
│  │  Vector     │  │  Keyword        │  │  Graph          │       │
│  │  Index      │  │  Index (BM25)   │  │  Index          │       │
│  │  (pgvector) │  │                 │  │  (KG)           │       │
│  └──────┬──────┘  └────────────────┘  └─────────────────┘       │
│         │                                                         │
│  ┌──────▼────────────────────────────────────────────────────┐   │
│  │              Storage Layer (civitas.storage)               │   │
│  │    PostgreSQL  ·  pgvector (HNSW)  ·  Document Store      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │               Ingestion Pipeline (civitas.ingestion)       │    │
│  │  Connectors → Parsers → Enricher → Quality Gate → Chunker │    │
│  └───────────────────────────────────────────────────────────┘    │
│                                                                     │
│  ┌─────────────────┐  ┌───────────────────┐  ┌───────────────┐   │
│  │   Governance    │  │    GraphRAG        │  │  Knowledge    │   │
│  │  (lifecycle,    │  │  (entities,        │  │  Organization │   │
│  │   quality,      │  │   communities,     │  │  (taxonomy,   │   │
│  │   audit, RBAC)  │  │   summaries)       │  │   spaces)     │   │
│  └─────────────────┘  └───────────────────┘  └───────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Core Design Principles

| Principle | Implementation |
|-----------|----------------|
| **Monorepo, no microservices** | All inter-module communication is direct Python calls — no REST, no gRPC, no queues |
| **Knowledge is governed** | Every document has lifecycle state, quality score, audit trail, and access control |
| **Multiple retrieval strategies** | Vector + BM25 + Graph → RRF Fusion → Cross-Encoder Reranking |
| **One public API** | `UnifiedKnowledgeEngine` is the only entry point for agents |
| **Agents never touch documents** | Agents call tools, tools call the engine, the engine handles everything |

---

## Repository Structure

```
CIVITAS-RAG/
│
├── civitas/                         # Main Python package
│   ├── core/                        # Domain foundation
│   │   ├── models/
│   │   │   ├── document.py          # Document entity (atomic knowledge unit)
│   │   │   ├── metadata.py          # Enterprise metadata model
│   │   │   ├── chunk.py             # DocumentChunk (indexable unit)
│   │   │   ├── knowledge_space.py   # KnowledgeSpace partition model
│   │   │   └── taxonomy.py          # TaxonomyNode + TaxonomyTree
│   │   ├── config/settings.py       # Pydantic settings (all env vars)
│   │   └── exceptions/              # Typed domain exceptions
│   │
│   ├── ingestion/                   # Document ingestion pipeline
│   │   ├── connectors/              # Source adapters (filesystem, API…)
│   │   ├── parsers/                 # Format parsers (PDF, DOCX, HTML, text)
│   │   ├── transformers/            # Chunker + Enricher
│   │   └── pipeline.py              # Orchestrator: blob → indexed chunks
│   │
│   ├── knowledge/                   # Knowledge organization
│   │   ├── taxonomy/registry.py     # Global taxonomy registry (100+ nodes)
│   │   └── spaces/registry.py       # KnowledgeSpace registry (6 default spaces)
│   │
│   ├── indexing/                    # LlamaIndex integration
│   │   ├── indexes/
│   │   │   ├── vector.py            # VectorStoreIndex + pgvector HNSW
│   │   │   ├── keyword.py           # BM25Retriever
│   │   │   ├── summary.py           # SummaryIndex + GraphKnowledgeIndex
│   │   │   └── base.py              # BaseKnowledgeIndex contract
│   │   └── registry.py              # IndexRegistry — fan-out to all indexes
│   │
│   ├── retrieval/                   # Retrieval layer
│   │   └── engine.py                # RetrievalEngine: RRF + reranking + ACL
│   │
│   ├── governance/                  # Governance layer
│   │   ├── lifecycle/               # State machine: DRAFT→ACTIVE→ARCHIVED
│   │   ├── quality/checker.py       # Multi-rule quality gate
│   │   ├── audit/logger.py          # Append-only audit trail → PostgreSQL
│   │   └── access/rbac.py           # 3-layer RBAC (platform, space, document)
│   │
│   ├── storage/                     # Storage adapters
│   │   ├── vector/pgvector.py       # pgvector schema management
│   │   └── document/postgres.py     # Document CRUD → PostgreSQL
│   │
│   ├── graphrag/                    # GraphRAG pipeline
│   │   ├── extractor.py             # Entity + relation extraction (LLM/spaCy)
│   │   ├── builder.py               # NetworkX graph + Leiden community detection
│   │   └── query.py                 # LOCAL + GLOBAL graph query modes
│   │
│   └── access/                      # Knowledge Access Layer
│       ├── contracts/query.py        # KnowledgeQuery / KnowledgeResponse
│       ├── engines/unified.py        # UnifiedKnowledgeEngine (sole agent API)
│       └── tools/base.py             # BaseKnowledgeTool + ToolRegistry
│
├── database/
│   └── migrations/
│       ├── 001_core_schema.sql       # Documents, spaces, taxonomy, chunks, indexes
│       ├── 002_vector_extensions.sql # pgvector, pg_trgm, HNSW index
│       ├── 003_graph_schema.sql      # Entities, relations, communities
│       └── 004_governance_schema.sql # Audit events, quality reports, views
│
├── config/
│   ├── default.yaml                  # Platform defaults
│   └── environments/
│       ├── development.yaml          # Dev overrides
│       └── production.yaml           # Production hardening
│
├── docs/
│   ├── architecture/
│   │   ├── ADR-001-monorepo.md       # Why monorepo
│   │   ├── ADR-002-knowledge-layers.md
│   │   └── ADR-003-multi-index.md    # Multi-index + RRF strategy
│   └── vision/platform-vision.md     # Full platform vision + roadmap
│
├── scripts/
│   ├── setup_database.py             # Run all migrations + create vector tables
│   └── ingest.py                     # CLI ingestion tool
│
└── tests/
    ├── unit/
    │   ├── test_models.py            # Domain model unit tests
    │   └── test_taxonomy.py          # Taxonomy, lifecycle, quality tests
    └── integration/
        └── test_pipeline.py          # Full ingestion pipeline tests
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 15+ with pgvector extension
- OpenAI API key (for embeddings + LLM features)

### 2. Installation

```bash
git clone https://github.com/amourgit/CIVITAS-RAG.git
cd CIVITAS-RAG
pip install -e ".[dev]"
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your database credentials and OpenAI API key
```

### 4. Initialize Database

```bash
python scripts/setup_database.py
```

This runs all four migrations and creates pgvector tables for each default knowledge space.

### 5. Ingest Documents

```bash
# Ingest a PDF into the legal-contracts space
python scripts/ingest.py \
  --path /data/legal/contract.pdf \
  --space legal-contracts \
  --domain legal \
  --category contracts \
  --taxonomy legal.contracts.service_agreements \
  --team legal-team

# Ingest a directory
python scripts/ingest.py \
  --path /data/hr/policies/ \
  --space hr-policies \
  --domain hr \
  --category policies \
  --auto-approve
```

### 6. Query Knowledge (Python API)

```python
from civitas.access.engines.unified import UnifiedKnowledgeEngine
from civitas.access.contracts.query import KnowledgeQuery, QueryIntent

# Build the engine (inject real dependencies in production)
engine = UnifiedKnowledgeEngine(retrieval_engine=retrieval_engine)

# Query
response = engine.query(KnowledgeQuery(
    query="What are our contractual obligations to ACME Corp?",
    intent=QueryIntent.REGULATORY,
    knowledge_space="legal-contracts",
    agent_id="legal-research-agent",
    top_k=5,
))

# Get LLM-ready context string
context = response.to_context_string()
print(f"Found {response.total_found} results in {response.latency_ms}ms")
```

### 7. Run Tests

```bash
make test-unit        # Fast, no DB required
make test-integration # Requires DB
```

---

## Knowledge Spaces

CIVITAS ships with six pre-configured knowledge spaces:

| Space | Domain | Index Strategy | GraphRAG | Default Access |
|-------|--------|---------------|----------|----------------|
| `global-shared` | global | hybrid | ✗ | All users |
| `legal-contracts` | legal | graph_enhanced | ✓ | Legal team |
| `finance-reports` | finance | hybrid | ✗ | Finance team |
| `hr-policies` | hr | hybrid | ✗ | All employees |
| `technical-docs` | technical | full | ✗ | Engineering |
| `external-knowledge` | external | semantic | ✗ | All users |

---

## Retrieval Strategies

| Strategy | Indexes | When to use |
|----------|---------|-------------|
| `semantic` | Vector only | Conceptual, paraphrase-tolerant queries |
| `keyword` | BM25 only | Exact terms, reference numbers, proper nouns |
| `hybrid` | Vector + BM25 → RRF | General purpose (default) |
| `graph_enhanced` | Vector + BM25 + Graph → RRF | Entity-rich domains (legal, finance) |
| `hierarchical` | Summary + Hierarchical | Long-form document comprehension |
| `full` | All → RRF → Reranker | Maximum recall at any cost |

---

## Governance Model

Every document goes through a governed lifecycle:

```
DRAFT → PENDING_REVIEW → IN_REVIEW → APPROVED → ACTIVE
                                              ↓
                                         DEPRECATED → ARCHIVED → PURGED
```

- **Quality Gate** — Documents below threshold are REJECTED before indexing
- **Audit Trail** — Every action is logged to `civitas_audit_events` (append-only)
- **RBAC** — Three layers: platform roles → space ACLs → document classification clearance
- **Retention** — Configurable retention policies (90d / 1y / 5y / permanent)

---

## GraphRAG Pipeline

For knowledge-dense spaces (e.g. `legal-contracts`), CIVITAS builds a knowledge graph:

1. **Extract** — LLM extracts entities (PERSON, ORG, CONCEPT, LAW…) and typed relations from chunks
2. **Build** — NetworkX constructs a weighted graph; edge weights accumulate from co-occurrence
3. **Detect** — Leiden algorithm (graspologic) detects semantic communities
4. **Summarize** — LLM generates a 2-3 sentence summary per community
5. **Query** — LOCAL mode (entity subgraph) or GLOBAL mode (community summaries)

---

## Future: LangGraph Agent Integration

```python
# Future LangGraph tool wrapping CIVITAS
from langchain.tools import tool
from civitas.access.tools.base import DomainSearchTool

tool_registry = ToolRegistry().build_default_tools(engine=engine, agent_id="research-agent")

@tool
def search_legal_knowledge(query: str) -> str:
    """Search legal documents for contracts, obligations, and compliance information."""
    return tool_registry.run("search_legal_knowledge", query)

# Agents call the tool → tool calls UnifiedKnowledgeEngine → engine handles everything
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Framework | Python 3.11 — Pure monorepo, no microservices |
| Knowledge Indexing | LlamaIndex 0.10+ |
| Vector Store | PostgreSQL 15 + pgvector (HNSW) |
| Embeddings | OpenAI text-embedding-3-small (configurable) |
| LLM | OpenAI GPT-4o-mini (configurable) |
| Full-Text Search | PostgreSQL GIN + pg_trgm |
| Graph Construction | NetworkX |
| Community Detection | Graspologic (Leiden algorithm) |
| Domain Models | Pydantic v2 |
| Database ORM | SQLAlchemy 2.0 |
| Future Agents | LangGraph (planned) |

---

## Architecture Decision Records

- [ADR-001 — Monorepo Structure](docs/architecture/ADR-001-monorepo.md)
- [ADR-002 — Knowledge Layer Architecture](docs/architecture/ADR-002-knowledge-layers.md)
- [ADR-003 — Multi-Index Strategy](docs/architecture/ADR-003-multi-index.md)

---

## Platform Vision

See [docs/vision/platform-vision.md](docs/vision/platform-vision.md) for the full platform philosophy, design principles, and multi-year roadmap.

---

## License

Proprietary — All rights reserved.

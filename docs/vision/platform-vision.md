# CIVITAS — Platform Vision

> *"A knowledge platform isn't a database. It's an institution."*

---

## What is CIVITAS?

**CIVITAS** (Comprehensive Intelligent Vector Index and Taxonomy Augmented System)
is an enterprise knowledge platform designed as the cognitive backbone of an
AI-native organization.

It is not a document storage system.  
It is not a search engine.  
It is a **governed, structured, traceable knowledge ecosystem** that serves
as the single source of organizational intelligence for future AI agents.

---

## The Problem We Solve

Organizations accumulate vast quantities of documents — contracts, policies,
reports, runbooks, research — scattered across file systems, email threads,
SharePoint folders, and siloed databases.

This knowledge is:
- **Unstructured** — no common classification or vocabulary
- **Ungoverned** — no lifecycle, no quality control, no access rules
- **Untrustworthy** — outdated documents coexist with current ones
- **Inaccessible** — agents cannot query it reliably or safely

CIVITAS transforms this raw material into **structured, governed, indexed knowledge**
that agents can trust and retrieve with precision.

---

## Architecture Philosophy

### Principle 1: Knowledge is a First-Class Citizen

Documents are not files. They are knowledge units with:
- Identity (unique ID, version history)
- Classification (domain, category, taxonomy path)
- Lifecycle (draft → active → archived)
- Ownership (who created it, who can access it)
- Quality (scored, gated, continuously assessed)

### Principle 2: One Platform, Many Consumers

The platform is designed to serve many concurrent agents with different needs:
- A legal research agent querying contract obligations
- A finance agent summarizing quarterly performance
- An HR agent answering policy questions
- An engineering agent retrieving runbooks during incidents

Each agent gets access only to what it needs, in the format it requires.

### Principle 3: Governance is Not Optional

Every document action is audited. Every state transition is recorded.
No document becomes accessible without passing a quality gate.
No agent retrieves a document it isn't authorized to see.

Governance is implemented at the platform level — agents cannot bypass it.

### Principle 4: Multiple Retrieval Strategies

No single retrieval strategy wins across all query types.
We maintain multiple specialized indexes (vector, keyword, graph, summary, hierarchical)
and fuse their results intelligently based on query intent.

### Principle 5: Evolution Without Disruption

The platform is designed to evolve:
- New document sources: implement `BaseConnector`
- New file formats: implement `BaseParser`
- New retrieval strategies: implement `BaseKnowledgeIndex`
- New agents: call `UnifiedKnowledgeEngine.query()`

No change to one component should require changes to another.

---

## Knowledge Space Architecture

The fundamental organizational unit is the **KnowledgeSpace** — a governed,
named partition of the knowledge base scoped to a domain or team.

```
CIVITAS Knowledge Platform
├── global-shared          ← All users, all agents
├── legal-contracts        ← Legal team, with GraphRAG enabled
├── finance-reports        ← Finance team
├── hr-policies            ← HR team, accessible to all employees
├── technical-docs         ← Engineering team, full index strategy
└── external-knowledge     ← Third-party, read-only
```

Each space has:
- Its own index strategy (semantic / hybrid / graph-enhanced / full)
- Its own quality gate thresholds
- Its own access control (teams, roles, agents)
- Its own audit trail

---

## The Knowledge Access Layer

The `UnifiedKnowledgeEngine` is the single entry point for all agents:

```python
# This is ALL an agent needs to know about CIVITAS
response = engine.query(KnowledgeQuery(
    query="What are our contractual obligations to ACME Corp?",
    intent=QueryIntent.REGULATORY,
    knowledge_space="legal-contracts",
    agent_id="legal-research-agent",
))

context = response.to_context_string()  # LLM-ready context block
```

The engine handles:
- Strategy selection (based on intent + space config)
- Multi-index retrieval and fusion
- GraphRAG context augmentation
- Access control enforcement
- Audit trail emission
- Result formatting for LLM consumption

---

## Roadmap

### Phase 1 (Current) — Knowledge Foundation
- [x] Core domain models (Document, Chunk, Metadata, KnowledgeSpace, Taxonomy)
- [x] Ingestion pipeline (connectors, parsers, chunkers, enrichers)
- [x] Multi-index architecture (Vector, Keyword, Summary, Graph)
- [x] Retrieval engine (RRF fusion + cross-encoder reranking)
- [x] Governance layer (lifecycle, quality, audit, RBAC)
- [x] GraphRAG pipeline (entity extraction, community detection)
- [x] Knowledge Access Layer (UnifiedKnowledgeEngine + tools)
- [x] PostgreSQL schema (core + vector + graph + governance)

### Phase 2 — Agent Integration
- [ ] LangGraph agent tools wrapping CIVITAS tools
- [ ] Streaming retrieval for long contexts
- [ ] Multi-space federated queries with score normalization
- [ ] Agent-specific knowledge spaces and memory

### Phase 3 — Scale & Operations
- [ ] Async ingestion pipeline (background processing)
- [ ] Incremental index updates (no full rebuilds)
- [ ] Knowledge freshness monitoring (automatic re-ingestion triggers)
- [ ] Multi-tenant isolation (namespace per organization)
- [ ] Observability dashboard (Prometheus metrics + Grafana)

### Phase 4 — Intelligence
- [ ] Automatic taxonomy classification (LLM + few-shot)
- [ ] Knowledge gap detection (what questions can't we answer?)
- [ ] Cross-document contradiction detection
- [ ] Proactive knowledge synthesis (automatic summaries of new content)
- [ ] Agent learning from retrieval feedback

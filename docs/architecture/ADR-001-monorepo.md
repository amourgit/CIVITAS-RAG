# ADR-001 — Monorepo Architecture

**Status:** Accepted  
**Date:** 2024-01  
**Deciders:** Platform Architects  

---

## Context

We are building an enterprise knowledge platform intended to serve multiple agents,
teams, and business domains over several years. The platform requires:

- Multiple interconnected components (ingestion, indexing, retrieval, governance)
- Fast iteration without deployment friction
- Low operational complexity in early phases
- Clear internal contracts between components
- Ability to extract components later if needed

## Decision

We adopt a **Python monorepo** architecture with the following constraints:

### What we DO

- Organize code in clearly bounded Python modules under `civitas/`
- Use direct Python function/class imports for all inter-component communication
- Define explicit contracts (Pydantic models) at every module boundary
- Maintain one `pyproject.toml` at the root
- Use dependency injection to keep modules testable in isolation

### What we DO NOT

- No microservices
- No internal REST APIs
- No internal gRPC
- No message queues (Kafka, RabbitMQ) for synchronous operations
- No separate deployable services per component

### Module boundaries

```
civitas/
├── core/          ← Domain models, settings, exceptions (no external deps)
├── ingestion/     ← File reading, parsing, chunking (pure Python)
├── knowledge/     ← Taxonomy, knowledge spaces (pure Python)
├── indexing/      ← LlamaIndex wrappers (LlamaIndex + PostgreSQL)
├── retrieval/     ← Query execution (LlamaIndex + PostgreSQL)
├── governance/    ← Lifecycle, quality, audit, RBAC (pure Python + PostgreSQL)
├── storage/       ← Database adapters (SQLAlchemy + pgvector)
├── graphrag/      ← Graph extraction, community detection (NetworkX)
└── access/        ← Knowledge Access Layer — the public API
```

Each module has a clearly defined responsibility. No module may import
from a module it conceptually "shouldn't know about":

- `core` imports from nowhere internal
- `ingestion` imports from `core` only
- `knowledge` imports from `core` only
- `indexing` imports from `core`, `knowledge`, `storage`
- `retrieval` imports from `core`, `indexing`
- `governance` imports from `core`
- `graphrag` imports from `core`, `storage`
- `access` imports from `core`, `retrieval`, `graphrag`

## Consequences

**Positive:**
- Zero network latency between components
- Simple deployment (one process)
- Easy debugging (single call stack)
- Low operational cost in early phases
- Fast test execution (no service mocking)

**Negative:**
- Cannot scale individual components independently (acceptable today)
- All components share the same process memory
- Cannot use different languages per component

**Mitigation:**
- Module boundaries are strict — a future extraction to services is possible
- The `civitas.access.engines.unified.UnifiedKnowledgeEngine` defines the exact
  API surface that would become an RPC boundary in a future services architecture

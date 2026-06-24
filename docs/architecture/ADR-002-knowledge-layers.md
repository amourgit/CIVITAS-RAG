# ADR-002 — Knowledge Layer Architecture

**Status:** Accepted  
**Date:** 2024-01  

---

## Context

The knowledge platform must serve multiple agent types with different information needs:
- Factual lookup ("What does clause 7 say?")
- Thematic exploration ("What are our main contractual risks?")
- Procedural queries ("How do I submit an expense report?")
- Regulatory queries ("What GDPR obligations apply to this use case?")

No single retrieval strategy performs best across all query types.

## Decision

We define five distinct knowledge layers, each with a clear responsibility:

### Layer 1 — Ingestion Layer (`civitas.ingestion`)
**Responsibility:** Transform raw files into structured Documents.

Pipeline: `Source → Connector → Parser → Enricher → Chunker → Document + Chunks`

Key design:
- Format-agnostic (PDF, DOCX, HTML, etc. all produce the same Document model)
- Quality gate on output (reject below threshold)
- Enrichment is deterministic (same input → same output)

### Layer 2 — Knowledge Organization Layer (`civitas.knowledge`)
**Responsibility:** Classify and organize documents in the taxonomy and knowledge spaces.

Every document belongs to:
1. Exactly one `KnowledgeSpace` (logical partition)
2. Exactly one `TaxonomyNode` leaf (classification)

This enables precise scoping at query time.

### Layer 3 — Index Layer (`civitas.indexing`)
**Responsibility:** Store chunks in specialized indexes for different retrieval patterns.

One knowledge space → multiple specialized indexes (see ADR-003).

### Layer 4 — Retrieval Layer (`civitas.retrieval`)
**Responsibility:** Execute queries across indexes and fuse results.

Strategy selection is configurable per space and per query intent.

### Layer 5 — Knowledge Access Layer (`civitas.access`)
**Responsibility:** Expose a typed, controlled API to future agents.

The `UnifiedKnowledgeEngine` is the ONLY public interface.
Agents never bypass it. This boundary is enforced by design.

## Information Flow

```
WRITE PATH
  File → Connector → Parser → Enricher → QualityChecker
       → LifecycleManager (DRAFT → PENDING_REVIEW → ACTIVE)
       → Chunker → IndexRegistry.add_chunks() → [Vector, Keyword, Graph, Summary] indexes
       → DocumentStore.save() → AuditLogger.log_ingestion()

READ PATH
  Agent → UnifiedKnowledgeEngine.query(KnowledgeQuery)
        → RetrievalEngine.retrieve(RetrievalQuery)
          → VectorIndex.get_retriever()  ──┐
          → KeywordIndex.get_retriever() ──┼→ RRF Fusion → Reranker
          → GraphIndex.get_retriever()   ──┘
        → AccessControl.filter()
        → GraphRAGQueryEngine.query() [optional]
        → AuditLogger.log_retrieval()
        → KnowledgeResponse
```

## Consequences

- Adding a new retrieval strategy requires only implementing `BaseKnowledgeIndex`
- Adding a new source requires only implementing `BaseConnector`
- The `KnowledgeQuery / KnowledgeResponse` contract is stable — agent tools don't change when we add strategies
- Access control is enforced at the `RetrievalEngine` level — never bypassed

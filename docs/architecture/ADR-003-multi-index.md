# ADR-003 — Multi-Index Strategy

**Status:** Accepted  
**Date:** 2024-01  

---

## Context

Different query types require different index types. A single vector index
cannot optimally serve all retrieval patterns in an enterprise knowledge platform.

## Decision

We maintain up to **five specialized indexes** per knowledge space,
each serving a distinct retrieval pattern:

### Index 1 — VectorKnowledgeIndex (`IndexType.VECTOR`)

**Backend:** LlamaIndex `VectorStoreIndex` + pgvector (HNSW)  
**Best for:** Semantic similarity — "what chunks are conceptually related to this query?"  
**Weakness:** Poor on exact matches, proper nouns, codes, reference numbers

Query example: *"What are the obligations of the service provider?"*

### Index 2 — KeywordKnowledgeIndex (`IndexType.KEYWORD`)

**Backend:** LlamaIndex `BM25Retriever` (rank_bm25)  
**Best for:** Exact-match — proper nouns, reference numbers, legal citations, code IDs  
**Weakness:** Cannot understand paraphrase or synonyms

Query example: *"Find document MSA-2024-ACME-001"*

### Index 3 — SummaryKnowledgeIndex (`IndexType.SUMMARY`)

**Backend:** LlamaIndex `SummaryIndex` (LLM-generated summaries)  
**Best for:** Document-level overview queries, routing, first-pass triage  
**Weakness:** Loses chunk-level detail

Query example: *"Which documents address GDPR compliance?"*

### Index 4 — GraphKnowledgeIndex (`IndexType.KNOWLEDGE_GRAPH`)

**Backend:** LlamaIndex `KnowledgeGraphIndex`  
**Best for:** Entity-centric queries, relationship traversal  
**Weakness:** Expensive to build; limited to extracted entity graph

Query example: *"What is Acme Corp's relationship with our legal team?"*

### Index 5 — HierarchicalIndex (`IndexType.HIERARCHICAL`)

**Backend:** LlamaIndex `HierarchicalNodeParser` (parent-child chunks)  
**Best for:** Long-form documents where context window matters  
**Weakness:** Higher storage overhead

Query example: *"Summarize the key terms of the service agreement"*

---

## Index Selection by Strategy

| Strategy          | Indexes Used                          | Use case                        |
|-------------------|---------------------------------------|---------------------------------|
| `SEMANTIC`        | Vector only                           | Pure semantic search            |
| `KEYWORD`         | Keyword only                          | Exact-match lookup              |
| `HYBRID`          | Vector + Keyword → RRF               | General purpose (default)       |
| `GRAPH_ENHANCED`  | Vector + Keyword + Graph → RRF       | Entity-rich domains (legal)     |
| `HIERARCHICAL`    | Summary + Hierarchical Vector        | Long-form document understanding|
| `FULL`            | All five → RRF → Reranker            | Maximum recall                  |

---

## Fusion Strategy: Reciprocal Rank Fusion (RRF)

When multiple indexes return results, we use RRF to merge ranked lists:

```
score(d) = Σ  1 / (k + rank_i(d))
          i
```

where `k=60` (standard constant), `rank_i(d)` is document `d`'s position in list `i`.

RRF advantages over score averaging:
- Score scales differ across indexes (cosine 0→1 vs BM25 0→∞)
- RRF normalizes by rank, not raw score
- Robust to any one index returning noisy results

---

## Reranking

After RRF, top-N results are reranked by a cross-encoder model:

```
VectorRetriever(top_k=20) ──┐
KeywordRetriever(top_k=20) ─┼→ RRF(top_k=40) → CrossEncoder → top_k=10
GraphRetriever(top_k=10)  ──┘
```

Cross-encoder: `cross-encoder/ms-marco-MiniLM-L-6-v2`  
(can be upgraded to ColBERT for higher quality)

---

## Per-Space Index Configuration

Not all spaces need all indexes. The `KnowledgeSpaceConfig` controls
which indexes are built and maintained per space:

```python
KnowledgeSpaceConfig(
    enabled_index_types=["vector", "keyword", "summary"],
    graph_rag_enabled=False,         # GraphRAG expensive — opt-in
    default_index_strategy=IndexStrategy.HYBRID,
)
```

GraphRAG is enabled only for knowledge-dense domains (e.g. `legal-contracts`)
where entity relationships provide significant retrieval lift.

"""
CIVITAS — Comprehensive Intelligent Vector Index and Taxonomy Augmented System

Enterprise Knowledge Platform built on:
  · LlamaIndex   — indexing, retrieval, query engines
  · PostgreSQL    — relational + document storage
  · pgvector      — high-performance vector similarity search
  · GraphRAG      — knowledge graph enrichment

Architecture: Python Monorepo — no microservices, no REST internal.
All inter-module communication is direct Python calls.

Modules:
  civitas.core        — domain models, config, exceptions
  civitas.ingestion   — document ingestion pipeline
  civitas.knowledge   — taxonomy, knowledge spaces
  civitas.indexing    — LlamaIndex index management
  civitas.retrieval   — multi-strategy retrieval engine
  civitas.governance  — lifecycle, quality, audit, access control
  civitas.storage     — vector, document, graph storage adapters
  civitas.graphrag    — GraphRAG entity/community pipeline
  civitas.access      — Knowledge Access Layer for future agents
"""

__version__ = "0.1.0"
__author__ = "CIVITAS Platform Team"

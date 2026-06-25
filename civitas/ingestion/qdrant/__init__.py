"""
civitas.ingestion.qdrant
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Système d'ingestion Qdrant pour CIVITAS.
"""

from civitas.ingestion.qdrant.client import CivitasQdrantClient, QdrantPoint, SearchResult
from civitas.ingestion.qdrant.config import (
    CollectionConfig,
    EmbeddingConfig,
    QdrantIngestionConfig,
    ScanConfig,
)
from civitas.ingestion.qdrant.embedder import DocumentEmbedder, TextChunker
from civitas.ingestion.qdrant.pipeline import (
    FileIngestionResult,
    QdrantIngestionPipeline,
    ScanIngestionReport,
)
from civitas.ingestion.qdrant.scanner import DiscoveredFile, FileScanner, ScanResult
from civitas.ingestion.qdrant.tracker import IngestionRecord, IngestionTracker

__all__ = [
    "QdrantIngestionPipeline", "ScanIngestionReport", "FileIngestionResult",
    "QdrantIngestionConfig", "ScanConfig", "CollectionConfig", "EmbeddingConfig",
    "CivitasQdrantClient", "QdrantPoint", "SearchResult",
    "FileScanner", "DiscoveredFile", "ScanResult",
    "DocumentEmbedder", "TextChunker",
    "IngestionTracker", "IngestionRecord",
]

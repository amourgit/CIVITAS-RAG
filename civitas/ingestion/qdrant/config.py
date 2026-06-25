"""
civitas.ingestion.qdrant.config
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Configuration du système d'ingestion Qdrant.

Toute la configuration est centralisée ici et peut être surchargée
via des fichiers YAML, des variables d'environnement ou programmatiquement.

Classes:
    QdrantIngestionConfig  — config globale du pipeline Qdrant
    CollectionConfig       — config d'une collection Qdrant cible
    ScanConfig             — config d'un scan (cible + options)
    EmbeddingConfig        — config du modèle d'embedding
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────
#  EMBEDDING CONFIG
# ─────────────────────────────────────────────────────────────

@dataclass
class EmbeddingConfig:
    """
    Configuration du modèle d'embedding.

    Providers supportés:
      - sentence-transformers (local, gratuit)
      - openai (API)
    """
    provider: str = "sentence-transformers"
    # Pour sentence-transformers — modèles recommandés:
    #   all-MiniLM-L6-v2          →  384 dims, très rapide
    #   all-mpnet-base-v2          →  768 dims, meilleure qualité
    #   paraphrase-multilingual-v3 →  768 dims, multilingue (FR+EN)
    model_name: str = "all-MiniLM-L6-v2"
    vector_size: int = 384           # Doit correspondre au modèle
    batch_size: int = 64             # Nombre de textes à embedder en une fois
    device: str = "cpu"              # cpu | cuda | mps
    normalize_embeddings: bool = True

    # OpenAI (si provider == "openai")
    openai_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY")
    )
    openai_model: str = "text-embedding-3-small"

    @classmethod
    def multilingual(cls) -> "EmbeddingConfig":
        """Profil multilingue (FR/EN/AR/ES...)."""
        return cls(
            model_name="paraphrase-multilingual-mpnet-base-v2",
            vector_size=768,
        )

    @classmethod
    def high_quality(cls) -> "EmbeddingConfig":
        """Profil haute qualité (plus lent mais précis)."""
        return cls(
            model_name="all-mpnet-base-v2",
            vector_size=768,
        )

    @classmethod
    def openai_small(cls) -> "EmbeddingConfig":
        """Profil OpenAI text-embedding-3-small."""
        return cls(
            provider="openai",
            model_name="text-embedding-3-small",
            vector_size=1536,
        )


# ─────────────────────────────────────────────────────────────
#  COLLECTION CONFIG
# ─────────────────────────────────────────────────────────────

@dataclass
class CollectionConfig:
    """
    Configuration d'une collection Qdrant.

    Chaque collection correspond à un domaine ou un groupe logique
    de documents (ex: ansible_docs, terraform_docs, cicd_docs...).
    """
    name: str                                   # Nom de la collection Qdrant
    description: str = ""                       # Description optionnelle
    distance: str = "Cosine"                    # Cosine | Euclid | Dot
    on_disk_payload: bool = True                # Stocker les payloads sur disque
    replication_factor: int = 1
    # Extensions autorisées pour CETTE collection (None = config globale)
    allowed_extensions: Optional[list[str]] = None
    # Taille de chunk spécifique à cette collection
    chunk_size: int = 512
    chunk_overlap: int = 64

    # Tags/métadonnées fixes injectés dans tous les points de cette collection
    default_tags: list[str] = field(default_factory=list)
    default_domain: str = ""

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "CollectionConfig":
        return cls(
            name=name,
            description=data.get("description", ""),
            distance=data.get("distance", "Cosine"),
            on_disk_payload=data.get("on_disk_payload", True),
            allowed_extensions=data.get("allowed_extensions"),
            chunk_size=data.get("chunk_size", 512),
            chunk_overlap=data.get("chunk_overlap", 64),
            default_tags=data.get("default_tags", []),
            default_domain=data.get("default_domain", ""),
        )


# ─────────────────────────────────────────────────────────────
#  SCAN CONFIG
# ─────────────────────────────────────────────────────────────

@dataclass
class ScanConfig:
    """
    Configuration d'un scan d'ingestion.

    Un scan = une cible (répertoire ou fichier) + une collection de destination.
    Plusieurs scans peuvent être lancés dans une même session.
    """
    # --- Cible ---
    source_path: str                            # Répertoire ou fichier à scanner
    collection_name: str                        # Collection Qdrant cible

    # --- Filtrage ---
    recursive: bool = True                      # Scanner récursivement
    allowed_extensions: Optional[list[str]] = None  # Surcharge la config globale
    excluded_patterns: list[str] = field(default_factory=lambda: [
        "*.tmp", "*.log", ".DS_Store", "~$*", "Thumbs.db", "*.pyc",
        "__pycache__", ".git", "*.swp",
    ])
    max_file_size_mb: float = 100.0

    # --- Pipeline ---
    chunk_size: int = 512
    chunk_overlap: int = 64
    dry_run: bool = False                       # Parser sans indexer

    # --- Métadonnées injectées ---
    domain: str = ""
    tags: list[str] = field(default_factory=list)

    # --- Déduplication ---
    skip_existing: bool = True                  # Ne pas réindexer les fichiers déjà traités

    @property
    def source_path_obj(self) -> Path:
        return Path(self.source_path)

    @classmethod
    def from_dict(cls, data: dict) -> "ScanConfig":
        return cls(
            source_path=data["source_path"],
            collection_name=data["collection_name"],
            recursive=data.get("recursive", True),
            allowed_extensions=data.get("allowed_extensions"),
            excluded_patterns=data.get("excluded_patterns", [
                "*.tmp", "*.log", ".DS_Store", "~$*",
            ]),
            max_file_size_mb=data.get("max_file_size_mb", 100.0),
            chunk_size=data.get("chunk_size", 512),
            chunk_overlap=data.get("chunk_overlap", 64),
            dry_run=data.get("dry_run", False),
            domain=data.get("domain", ""),
            tags=data.get("tags", []),
            skip_existing=data.get("skip_existing", True),
        )


# ─────────────────────────────────────────────────────────────
#  GLOBAL INGESTION CONFIG
# ─────────────────────────────────────────────────────────────

# Extensions supportées par défaut (inclut les fichiers DevOps/IaC)
DEFAULT_ALLOWED_EXTENSIONS = [
    # Documents bureautiques
    ".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm",
    ".csv", ".json", ".xml", ".xlsx", ".pptx",
    # Code & IaC (pour DevOps)
    ".yml", ".yaml", ".tf", ".tfvars", ".sh", ".conf", ".config",
    ".ini", ".toml", ".env", ".properties",
    # Fichiers texte génériques sans extension particulière
    "Jenkinsfile", "Dockerfile", "Makefile",
]


@dataclass
class QdrantIngestionConfig:
    """
    Configuration globale du système d'ingestion Qdrant.

    Peut être chargée depuis un fichier YAML (voir QdrantIngestionConfig.from_yaml).
    """

    # --- Connexion Qdrant ---
    qdrant_host: str = field(
        default_factory=lambda: os.getenv("QDRANT_HOST", "localhost")
    )
    qdrant_port: int = field(
        default_factory=lambda: int(os.getenv("QDRANT_PORT", "6333"))
    )
    qdrant_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("QDRANT_API_KEY")
    )
    qdrant_url: Optional[str] = field(
        default_factory=lambda: os.getenv("QDRANT_URL")
    )  # Si Qdrant Cloud: https://xxx.cloud.qdrant.io
    qdrant_in_memory: bool = field(
        default_factory=lambda: os.getenv("QDRANT_IN_MEMORY", "false").lower() == "true"
    )  # Mode in-memory (tests, dev sans serveur)

    # --- Embedding ---
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)

    # --- Collections définies ---
    collections: dict[str, CollectionConfig] = field(default_factory=dict)

    # --- Extensions par défaut ---
    allowed_extensions: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_EXTENSIONS)
    )

    # --- Chunking par défaut ---
    default_chunk_size: int = 512
    default_chunk_overlap: int = 64

    # --- Tracking (déduplication) ---
    # Fichier SQLite local pour tracker les fichiers déjà ingérés
    tracker_db_path: str = field(
        default_factory=lambda: os.getenv(
            "CIVITAS_TRACKER_DB", ".civitas_ingestion_tracker.db"
        )
    )

    # --- Concurrence ---
    max_workers: int = 4
    batch_size: int = 50              # Nombre de points Qdrant par batch d'upsert

    # --- Logging ---
    log_level: str = "INFO"
    log_file: Optional[str] = None

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "QdrantIngestionConfig":
        """Charger la configuration depuis un fichier YAML."""
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "QdrantIngestionConfig":
        qdrant_cfg = data.get("qdrant", {})
        emb_cfg = data.get("embedding", {})
        coll_cfg = data.get("collections", {})
        ing_cfg = data.get("ingestion", {})

        embedding = EmbeddingConfig(
            provider=emb_cfg.get("provider", "sentence-transformers"),
            model_name=emb_cfg.get("model_name", "all-MiniLM-L6-v2"),
            vector_size=emb_cfg.get("vector_size", 384),
            batch_size=emb_cfg.get("batch_size", 64),
            device=emb_cfg.get("device", "cpu"),
            normalize_embeddings=emb_cfg.get("normalize_embeddings", True),
            openai_model=emb_cfg.get("openai_model", "text-embedding-3-small"),
        )

        collections = {
            name: CollectionConfig.from_dict(name, cfg)
            for name, cfg in coll_cfg.items()
        }

        return cls(
            qdrant_host=qdrant_cfg.get("host", os.getenv("QDRANT_HOST", "localhost")),
            qdrant_port=int(qdrant_cfg.get("port", os.getenv("QDRANT_PORT", "6333"))),
            qdrant_api_key=qdrant_cfg.get("api_key", os.getenv("QDRANT_API_KEY")),
            qdrant_url=qdrant_cfg.get("url", os.getenv("QDRANT_URL")),
            embedding=embedding,
            collections=collections,
            allowed_extensions=ing_cfg.get(
                "allowed_extensions", list(DEFAULT_ALLOWED_EXTENSIONS)
            ),
            default_chunk_size=ing_cfg.get("chunk_size", 512),
            default_chunk_overlap=ing_cfg.get("chunk_overlap", 64),
            tracker_db_path=ing_cfg.get(
                "tracker_db_path",
                os.getenv("CIVITAS_TRACKER_DB", ".civitas_ingestion_tracker.db"),
            ),
            max_workers=ing_cfg.get("max_workers", 4),
            batch_size=ing_cfg.get("batch_size", 50),
            log_level=data.get("log_level", "INFO"),
            log_file=data.get("log_file"),
        )

    def get_collection(self, name: str) -> CollectionConfig:
        """Retourne la config d'une collection (crée une par défaut si inexistante)."""
        if name not in self.collections:
            self.collections[name] = CollectionConfig(name=name)
        return self.collections[name]

    def effective_extensions(self, scan: ScanConfig) -> list[str]:
        """Retourne les extensions effectives pour un scan donné."""
        if scan.allowed_extensions:
            return scan.allowed_extensions
        col = self.collections.get(scan.collection_name)
        if col and col.allowed_extensions:
            return col.allowed_extensions
        return self.allowed_extensions

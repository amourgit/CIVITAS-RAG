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
      - sentence-transformers  — local, gratuit, recommandé en production
      - openai                 — API (nécessite OPENAI_API_KEY)
      - tfidf-local            — offline/test, sans réseau, basé scikit-learn
    """
    provider: str = "sentence-transformers"
    # Pour sentence-transformers — modèles recommandés:
    #   all-MiniLM-L6-v2                    →  384 dims, très rapide
    #   all-mpnet-base-v2                   →  768 dims, meilleure qualité
    #   paraphrase-multilingual-mpnet-base-v2 → 768 dims, multilingue (FR+EN+ES...)
    model_name: str = "all-MiniLM-L6-v2"
    vector_size: int = 384           # DOIT correspondre au modèle
    batch_size: int = 64             # Textes à embedder en une passe
    device: str = "cpu"              # cpu | cuda | mps
    normalize_embeddings: bool = True

    # OpenAI (si provider == "openai")
    openai_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY")
    )
    openai_model: str = "text-embedding-3-small"

    # ── Profils prêts à l'emploi ──────────────────────────────

    @classmethod
    def multilingual(cls) -> "EmbeddingConfig":
        """Profil multilingue (FR/EN/AR/ES...) — 768 dims."""
        return cls(
            model_name="paraphrase-multilingual-mpnet-base-v2",
            vector_size=768,
        )

    @classmethod
    def high_quality(cls) -> "EmbeddingConfig":
        """Profil haute qualité (plus lent mais meilleur) — 768 dims."""
        return cls(
            model_name="all-mpnet-base-v2",
            vector_size=768,
        )

    @classmethod
    def openai_small(cls) -> "EmbeddingConfig":
        """Profil OpenAI text-embedding-3-small — 1536 dims."""
        return cls(
            provider="openai",
            model_name="text-embedding-3-small",
            vector_size=1536,
        )

    @classmethod
    def offline_test(cls, vector_size: int = 128) -> "EmbeddingConfig":
        """Profil offline pour tests/CI sans accès réseau — TF-IDF + SVD."""
        return cls(
            provider="tfidf-local",
            model_name="tfidf",
            vector_size=vector_size,
            batch_size=32,
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
    description: str = ""                       # Description libre
    distance: str = "Cosine"                    # Cosine | Euclid | Dot
    on_disk_payload: bool = True                # Payloads stockés sur disque (prod)
    replication_factor: int = 1                 # > 1 pour cluster multi-nœuds

    # Extensions autorisées pour CETTE collection (None = hérite config globale)
    allowed_extensions: Optional[list[str]] = None

    # Chunking spécifique à cette collection (surcharge config globale)
    chunk_size: int = 512
    chunk_overlap: int = 64

    # Métadonnées injectées automatiquement dans tous les points
    default_tags: list[str] = field(default_factory=list)
    default_domain: str = ""

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "CollectionConfig":
        return cls(
            name=name,
            description=data.get("description", ""),
            distance=data.get("distance", "Cosine"),
            on_disk_payload=data.get("on_disk_payload", True),
            replication_factor=data.get("replication_factor", 1),
            allowed_extensions=data.get("allowed_extensions"),
            chunk_size=data.get("chunk_size", 512),
            chunk_overlap=data.get("chunk_overlap", 64),
            default_tags=data.get("default_tags", []),
            default_domain=data.get("default_domain", ""),
        )


# ─────────────────────────────────────────────────────────────
#  SCAN CONFIG
# ─────────────────────────────────────────────────────────────

# Patterns exclus par défaut (couvre les artefacts system + dev courants)
DEFAULT_EXCLUDED_PATTERNS: list[str] = [
    "*.tmp", "*.log", ".DS_Store", "~$*", "Thumbs.db",
    "*.pyc", "*.pyo", "__pycache__", ".git", ".svn",
    "*.swp", "*.swo", "node_modules", ".venv", "venv",
    "*.egg-info", ".mypy_cache", ".pytest_cache", ".ruff_cache",
]


@dataclass
class ScanConfig:
    """
    Configuration d'un scan d'ingestion.

    Un scan = une cible (répertoire ou fichier) + une collection Qdrant
    + options de filtrage, chunking et métadonnées.
    Plusieurs scans peuvent être lancés dans une même session.
    """
    # --- Cible ---
    source_path: str                            # Répertoire ou fichier à scanner
    collection_name: str                        # Collection Qdrant cible

    # --- Filtrage ---
    recursive: bool = True                      # Scanner récursivement
    allowed_extensions: Optional[list[str]] = None  # Surcharge config globale si défini
    excluded_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXCLUDED_PATTERNS)  # FIX: copie complète
    )
    max_file_size_mb: float = 100.0

    # --- Pipeline ---
    chunk_size: int = 512
    chunk_overlap: int = 64
    dry_run: bool = False                       # Analyse sans indexer dans Qdrant

    # --- Métadonnées injectées dans les points ---
    domain: str = ""
    tags: list[str] = field(default_factory=list)

    # --- Déduplication ---
    skip_existing: bool = True                  # Sauter les fichiers inchangés

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
            # FIX: si absent du YAML → utiliser la liste complète par défaut
            excluded_patterns=data.get("excluded_patterns", list(DEFAULT_EXCLUDED_PATTERNS)),
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

# Extensions supportées par défaut (DevOps/IaC + documents bureautiques)
DEFAULT_ALLOWED_EXTENSIONS: list[str] = [
    # Documents
    ".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm",
    ".csv", ".json", ".xml", ".xlsx", ".pptx",
    # IaC & Config
    ".yml", ".yaml", ".tf", ".tfvars", ".sh", ".conf", ".config",
    ".ini", ".toml", ".env", ".properties",
    # Fichiers reconnus sans extension (par nom exact)
    "Jenkinsfile", "Dockerfile", "Makefile", "Vagrantfile",
]


@dataclass
class QdrantIngestionConfig:
    """
    Configuration globale du système d'ingestion Qdrant.

    Peut être chargée depuis un fichier YAML via from_yaml(),
    ou instanciée programmatiquement avec des valeurs par défaut.
    Toutes les valeurs sont surchargeable via variables d'environnement.
    """

    # --- Connexion Qdrant ---
    qdrant_host: str = field(
        default_factory=lambda: os.getenv("QDRANT_HOST", "localhost")
    )
    qdrant_port: int = field(
        default_factory=lambda: int(os.getenv("QDRANT_PORT", "6333"))
    )
    # IMPORTANT: filtrer la string vide — QDRANT_API_KEY= vide doit être None,
    # sinon qdrant-client active HTTPS automatiquement (api_key is not None → https=True)
    qdrant_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("QDRANT_API_KEY") or None
    )
    # URL complète (Qdrant Cloud) — prend la priorité sur host+port
    # IMPORTANT: os.getenv retourne '' si la var est définie mais vide → on filtre
    qdrant_url: Optional[str] = field(
        default_factory=lambda: os.getenv("QDRANT_URL") or None
    )
    # Mode in-memory (tests, dev sans serveur Qdrant)
    qdrant_in_memory: bool = field(
        default_factory=lambda: os.getenv("QDRANT_IN_MEMORY", "false").lower() == "true"
    )

    # --- Embedding ---
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)

    # --- Collections définies ---
    collections: dict[str, CollectionConfig] = field(default_factory=dict)

    # --- Extensions par défaut (héritées si scan/collection ne définissent pas les leurs) ---
    allowed_extensions: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_EXTENSIONS)
    )

    # --- Chunking par défaut ---
    default_chunk_size: int = 512
    default_chunk_overlap: int = 64

    # --- Tracking (déduplication via SQLite) ---
    tracker_db_path: str = field(
        default_factory=lambda: os.getenv(
            "CIVITAS_TRACKER_DB", ".civitas_ingestion_tracker.db"
        )
    )

    # --- Perf ---
    max_workers: int = 4           # Réservé pour parallélisation future
    batch_size: int = 50           # Points Qdrant par batch d'upsert

    # --- Logging ---
    log_level: str = "INFO"
    log_file: Optional[str] = None

    # ── YAML Loading ──────────────────────────────────────────

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "QdrantIngestionConfig":
        """Charger la configuration depuis un fichier YAML."""
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls._from_dict(data or {})

    @classmethod
    def _from_dict(cls, data: dict) -> "QdrantIngestionConfig":
        qdrant_cfg = data.get("qdrant", {})
        emb_cfg    = data.get("embedding", {})
        coll_cfg   = data.get("collections", {})
        ing_cfg    = data.get("ingestion", {})

        embedding = EmbeddingConfig(
            provider=emb_cfg.get("provider", "sentence-transformers"),
            model_name=emb_cfg.get("model_name", "all-MiniLM-L6-v2"),
            vector_size=emb_cfg.get("vector_size", 384),
            batch_size=emb_cfg.get("batch_size", 64),
            device=emb_cfg.get("device", "cpu"),
            normalize_embeddings=emb_cfg.get("normalize_embeddings", True),
            # FIX: openai_api_key transmis depuis le YAML (était absent)
            openai_api_key=emb_cfg.get("openai_api_key", os.getenv("OPENAI_API_KEY")),
            openai_model=emb_cfg.get("openai_model", "text-embedding-3-small"),
        )

        collections = {
            name: CollectionConfig.from_dict(name, cfg)
            for name, cfg in (coll_cfg or {}).items()
        }

        return cls(
            qdrant_host=qdrant_cfg.get("host", os.getenv("QDRANT_HOST", "localhost")),
            qdrant_port=int(qdrant_cfg.get("port", os.getenv("QDRANT_PORT", "6333"))),
            qdrant_api_key=qdrant_cfg.get("api_key", os.getenv("QDRANT_API_KEY")),
            qdrant_url=qdrant_cfg.get("url") or os.getenv("QDRANT_URL") or None,
            qdrant_in_memory=qdrant_cfg.get(
                "in_memory",
                os.getenv("QDRANT_IN_MEMORY", "false").lower() == "true",
            ),
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

    # ── Helpers ───────────────────────────────────────────────

    def get_collection(self, name: str) -> CollectionConfig:
        """Retourne la config d'une collection (auto-créée par défaut si absente)."""
        if name not in self.collections:
            self.collections[name] = CollectionConfig(name=name)
        return self.collections[name]

    def effective_extensions(self, scan: "ScanConfig") -> list[str]:
        """
        Résout les extensions effectives pour un scan donné.

        Priorité: scan.allowed_extensions > collection.allowed_extensions > global.
        """
        if scan.allowed_extensions:
            return scan.allowed_extensions
        col = self.collections.get(scan.collection_name)
        if col and col.allowed_extensions:
            return col.allowed_extensions
        return self.allowed_extensions

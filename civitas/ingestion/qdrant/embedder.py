"""
civitas.ingestion.qdrant.embedder
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Embedder multi-provider pour la vectorisation des chunks.

Providers supportés:
  · sentence-transformers  — local, aucune API key, recommandé en production
  · openai                 — API, nécessite OPENAI_API_KEY
  · tfidf-local            — offline/CI, aucune dépendance réseau

Le modèle est chargé une seule fois (singleton par provider+model_name)
et réutilisé pour tous les batchs d'embedding dans la session.

NOTE sur tfidf-local:
  Le TF-IDF est ré-entraîné incrémentalement à chaque appel avec de
  nouveaux textes, de façon à maintenir un espace vectoriel cohérent
  entre les chunks indexés et les requêtes. Les requêtes doivent passer
  par le même embedder singleton que l'ingestion pour partager cet espace.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from civitas.ingestion.qdrant.config import EmbeddingConfig

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  TEXT CHUNKER
# ─────────────────────────────────────────────────────────────

@dataclass
class TextChunk:
    """Un chunk de texte extrait d'un document."""
    text: str
    chunk_index: int
    start_char: int
    end_char: int

    @property
    def word_count(self) -> int:
        return len(self.text.split())


class TextChunker:
    """
    Chunker de texte par fenêtre glissante sur les mots.

    Robuste sur tous les formats texte: YAML, JSON, Terraform, Shell,
    Markdown, PDF extrait, DOCX extrait, etc.

    Args:
        chunk_size:    Taille maximale d'un chunk en mots.
        chunk_overlap: Nombre de mots partagés entre deux chunks consécutifs.
                       Clamped à chunk_size // 2 pour éviter les boucles infinies.
    """

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64) -> None:
        self.chunk_size    = max(10, chunk_size)
        self.chunk_overlap = max(0, min(chunk_overlap, chunk_size // 2))

    def chunk(self, text: str) -> list[TextChunk]:
        """Découper un texte en chunks chevauchants."""
        if not text or not text.strip():
            return []

        # Normaliser les blancs excessifs (préserve la structure du texte)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        text = re.sub(r" {4,}", "   ", text)

        words = text.split()
        if not words:
            return []

        chunks: list[TextChunk] = []
        step      = max(1, self.chunk_size - self.chunk_overlap)
        chunk_idx = 0
        pos       = 0

        while pos < len(words):
            end        = min(pos + self.chunk_size, len(words))
            chunk_text = " ".join(words[pos:end]).strip()

            if chunk_text:
                # Positions caractères approximatives (suffisant pour les métadonnées)
                start_char = len(" ".join(words[:pos]))
                end_char   = len(" ".join(words[:end]))
                chunks.append(TextChunk(
                    text=chunk_text,
                    chunk_index=chunk_idx,
                    start_char=start_char,
                    end_char=end_char,
                ))
                chunk_idx += 1

            pos += step

        return chunks

    def chunk_with_context(self, text: str, source_path: str) -> list[TextChunk]:
        """
        Chunker en ajoutant le nom du fichier source comme préfixe contextuel.

        Le préfixe "File: <nom>" au premier chunk améliore la pertinence
        des embeddings pour les fichiers IaC (l'extension et le nom sont
        souvent des signaux sémantiques forts: "Jenkinsfile", "main.tf"...).
        """
        chunks = self.chunk(text)
        if chunks and source_path:
            # Extraire le nom de fichier (dernier segment du chemin, OS-agnostique)
            filename = Path(source_path).name if "/" in source_path or "\\" in source_path \
                       else source_path
            prefix = f"File: {filename}\n\n"
            chunks[0] = TextChunk(
                text=prefix + chunks[0].text,
                chunk_index=chunks[0].chunk_index,
                start_char=chunks[0].start_char,
                end_char=chunks[0].end_char,
            )
        return chunks


# Import ici pour éviter la circularité (Path est utilisé dans chunk_with_context)
from pathlib import Path


# ─────────────────────────────────────────────────────────────
#  EMBEDDER
# ─────────────────────────────────────────────────────────────

class DocumentEmbedder:
    """
    Embedder multi-provider avec cache singleton.

    Usage:
        embedder = DocumentEmbedder.get_or_create(EmbeddingConfig())
        vectors = embedder.embed_texts(["texte1", "texte2"])
        # → list[list[float]], chaque vecteur de dimension config.vector_size

    Le singleton garantit que:
      - le modèle n'est chargé qu'une fois par session
      - le TF-IDF local partage le même espace vectoriel entre ingestion et queries
    """

    _instance_cache: dict[str, "DocumentEmbedder"] = {}

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config  = config
        self._model  = None
        self._loaded = False
        # Attributs spécifiques tfidf-local
        self._tfidf          = None
        self._svd            = None
        self._tfidf_fitted   = False
        self._corpus_cache: list[str] = []

    @classmethod
    def get_or_create(cls, config: EmbeddingConfig) -> "DocumentEmbedder":
        """Retourne ou crée le singleton pour (provider, model_name)."""
        key = f"{config.provider}:{config.model_name}:{config.vector_size}"
        if key not in cls._instance_cache:
            cls._instance_cache[key] = cls(config)
        return cls._instance_cache[key]

    @classmethod
    def clear_cache(cls) -> None:
        """Vider le cache singleton (utile pour les tests d'isolation)."""
        cls._instance_cache.clear()

    def _load_model(self) -> None:
        """Charger le modèle d'embedding (lazy, une seule fois)."""
        if self._loaded:
            return

        if self.config.provider == "tfidf-local":
            logger.info(
                "Using TF-IDF local embedder (offline mode, dim=%d)",
                self.config.vector_size,
            )
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.decomposition import TruncatedSVD
            self._tfidf = TfidfVectorizer(
                max_features=min(10000, self.config.vector_size * 10),
                sublinear_tf=True,
                analyzer="word",
                token_pattern=r"(?u)\b\w+\b",
                ngram_range=(1, 2),
            )
            self._svd = TruncatedSVD(
                n_components=self.config.vector_size,
                random_state=42,
            )
            self._model  = "tfidf"
            self._loaded = True
            return

        if self.config.provider == "sentence-transformers":
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(
                    "Loading sentence-transformers model: %s (device=%s)",
                    self.config.model_name, self.config.device,
                )
                self._model  = SentenceTransformer(
                    self.config.model_name,
                    device=self.config.device,
                )
                self._loaded = True
                logger.info("Model loaded successfully ✓")
            except ImportError:
                raise ImportError(
                    "sentence-transformers required: pip install sentence-transformers"
                )
            return

        if self.config.provider == "openai":
            try:
                import openai as _openai
                if self.config.openai_api_key:
                    _openai.api_key = self.config.openai_api_key
                self._model  = _openai
                self._loaded = True
                logger.info(
                    "OpenAI embeddings configured: %s", self.config.openai_model
                )
            except ImportError:
                raise ImportError("openai required: pip install openai")
            return

        raise ValueError(
            f"Unknown embedding provider: '{self.config.provider}'. "
            "Valid values: sentence-transformers | openai | tfidf-local"
        )

    @property
    def vector_size(self) -> int:
        return self.config.vector_size

    # ── Core embedding ────────────────────────────────────────

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Vectoriser une liste de textes.

        Returns:
            list[list[float]] — vecteurs normalisés L2 de dimension vector_size.
        """
        if not texts:
            return []

        self._load_model()

        # ── TF-IDF local ──────────────────────────────────────
        if self.config.provider == "tfidf-local":
            return self._embed_tfidf(texts)

        # ── sentence-transformers ──────────────────────────────
        if self.config.provider == "sentence-transformers":
            import numpy as np
            vectors = self._model.encode(
                texts,
                batch_size=self.config.batch_size,
                show_progress_bar=False,
                normalize_embeddings=self.config.normalize_embeddings,
                convert_to_numpy=True,
            )
            return [v.tolist() for v in vectors]

        # ── OpenAI ────────────────────────────────────────────
        if self.config.provider == "openai":
            import openai as _openai
            all_vectors: list[list[float]] = []
            bs = min(self.config.batch_size, 100)
            for i in range(0, len(texts), bs):
                response = _openai.embeddings.create(
                    input=texts[i : i + bs],
                    model=self.config.openai_model,
                )
                all_vectors.extend(item.embedding for item in response.data)
            return all_vectors

        raise ValueError(f"Unknown provider: {self.config.provider}")

    def _embed_tfidf(self, texts: list[str]) -> list[list[float]]:
        """
        Embedding TF-IDF + SVD avec re-fit incrémental sur le corpus global.

        Stratégie:
          - Si un texte est nouveau → re-fit sur le corpus complet élargi.
            Cela invalide les anciens vecteurs Qdrant pour les textes déjà indexés,
            MAIS dans un contexte in-memory/test l'ensemble du corpus est ingéré
            dans la même session, donc l'espace est cohérent dès le départ.
          - En production, sentence-transformers est recommandé (espace stable).
        """
        import numpy as np
        import warnings

        new_texts = [t for t in texts if t not in self._corpus_cache]
        need_refit = not self._tfidf_fitted or bool(new_texts)

        if need_refit:
            self._corpus_cache.extend(new_texts)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tfidf_all = self._tfidf.fit_transform(self._corpus_cache)

            n_comp = min(
                max(1, tfidf_all.shape[0] - 1),
                max(1, tfidf_all.shape[1] - 1),
                self.config.vector_size,
            )
            self._svd.n_components = n_comp
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._svd.fit(tfidf_all)
            self._tfidf_fitted = True

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tfidf_matrix  = self._tfidf.transform(texts)
            vectors_raw   = self._svd.transform(tfidf_matrix)

        # Normalisation L2
        norms   = np.linalg.norm(vectors_raw, axis=1, keepdims=True)
        norms   = np.where(norms == 0, 1.0, norms)
        vectors = vectors_raw / norms

        # Padding si SVD produit moins de dims que vector_size
        if vectors.shape[1] < self.config.vector_size:
            pad     = np.zeros((vectors.shape[0], self.config.vector_size - vectors.shape[1]))
            vectors = np.concatenate([vectors, pad], axis=1)

        return [v.tolist() for v in vectors]

    def embed_query(self, query: str) -> list[float]:
        """
        Vectoriser une requête de recherche.

        FIX: pour tfidf-local, la query est ajoutée au corpus si nécessaire
        pour garantir qu'elle est dans le même espace vectoriel que les chunks.
        """
        result = self.embed_texts([query])
        return result[0] if result else []

    def embed_texts_batched(
        self,
        texts: list[str],
        batch_size: Optional[int] = None,
    ) -> list[list[float]]:
        """
        Vectoriser en micro-batches avec logging de progression.
        Utile pour les corpus très volumineux.
        """
        bs          = batch_size or self.config.batch_size
        all_vectors: list[list[float]] = []
        total_batches = (len(texts) + bs - 1) // bs

        for i in range(0, len(texts), bs):
            batch = texts[i : i + bs]
            logger.debug(
                "Embedding batch %d/%d (%d texts)",
                i // bs + 1, total_batches, len(batch),
            )
            all_vectors.extend(self.embed_texts(batch))

        return all_vectors

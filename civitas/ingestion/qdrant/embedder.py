"""
civitas.ingestion.qdrant.embedder
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Embedder multi-provider pour la vectorisation des chunks.

Providers supportés:
  · sentence-transformers (local, aucune API key, gratuit)
  · openai (API, nécessite OPENAI_API_KEY)

Le modèle est chargé une seule fois (singleton par provider/model)
et réutilisé pour tous les batchs d'embedding.

Chunking intégré:
  · SentenceChunker simple (split par phrases + window sliding)
  · Compatible avec les fichiers texte, yaml, json, tf, sh...
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
    Chunker de texte simple et robuste.

    Stratégie: découpage par fenêtre glissante sur les tokens (mots).
    Gère bien les fichiers YAML, JSON, Terraform, Shell, Markdown, etc.

    Args:
        chunk_size:    Taille max en mots (pas en tokens) — approx.
        chunk_overlap: Chevauchement en mots entre chunks consécutifs.
    """

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64) -> None:
        # Convertir de tokens approx en mots (1 token ≈ 0.75 mot)
        self.chunk_size = max(50, chunk_size)
        self.chunk_overlap = max(0, min(chunk_overlap, chunk_size // 2))

    def chunk(self, text: str) -> list[TextChunk]:
        """Découper un texte en chunks chevauchants."""
        if not text or not text.strip():
            return []

        # Normaliser les espaces et sauts de ligne excessifs
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        text = re.sub(r" {4,}", "   ", text)

        # Tokenisation simple par mots (préserve la ponctuation et les newlines)
        words = text.split()
        if not words:
            return []

        chunks: list[TextChunk] = []
        step = max(1, self.chunk_size - self.chunk_overlap)
        chunk_idx = 0
        pos = 0

        while pos < len(words):
            end = min(pos + self.chunk_size, len(words))
            chunk_words = words[pos:end]
            chunk_text = " ".join(chunk_words).strip()

            if chunk_text:
                # Calculer les positions caractères approximatives
                start_char = len(" ".join(words[:pos]))
                end_char = len(" ".join(words[:end]))

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
        Chunker en ajoutant le chemin source comme préfixe de contexte.
        Améliore la qualité des embeddings pour les fichiers IaC.
        """
        chunks = self.chunk(text)
        # Ajouter le chemin comme contexte au premier chunk
        if chunks and source_path:
            filename = source_path.split("/")[-1] if "/" in source_path else source_path
            prefix = f"File: {filename}\n\n"
            chunks[0] = TextChunk(
                text=prefix + chunks[0].text,
                chunk_index=chunks[0].chunk_index,
                start_char=chunks[0].start_char,
                end_char=chunks[0].end_char,
            )
        return chunks


# ─────────────────────────────────────────────────────────────
#  EMBEDDER
# ─────────────────────────────────────────────────────────────

class DocumentEmbedder:
    """
    Embedder multi-provider.

    Usage:
        embedder = DocumentEmbedder(EmbeddingConfig())
        vectors = embedder.embed_texts(["text1", "text2", "text3"])
        # → list of np.ndarray shape (vector_size,)
    """

    _instance_cache: dict[str, "DocumentEmbedder"] = {}

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self._model = None
        self._loaded = False

    @classmethod
    def get_or_create(cls, config: EmbeddingConfig) -> "DocumentEmbedder":
        """Cache singleton par (provider, model_name)."""
        key = f"{config.provider}:{config.model_name}"
        if key not in cls._instance_cache:
            cls._instance_cache[key] = cls(config)
        return cls._instance_cache[key]

    def _load_model(self) -> None:
        """Charger le modèle d'embedding (lazy loading)."""
        if self._loaded:
            return

        if self.config.provider == "tfidf-local":
            # Provider offline — TF-IDF vectorizer (tests, environnements sans réseau)
            # Produit des vecteurs denses via SVD, aucune dépendance réseau.
            logger.info(
                "Using TF-IDF local embedder (offline mode, dim=%d)", self.config.vector_size
            )
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.decomposition import TruncatedSVD
            import numpy as np

            self._tfidf = TfidfVectorizer(
                max_features=min(10000, self.config.vector_size * 10),
                sublinear_tf=True,
                analyzer="word",
                token_pattern=r"(?u)\b\w+\b",
                ngram_range=(1, 2),
            )
            self._svd = TruncatedSVD(n_components=self.config.vector_size, random_state=42)
            self._tfidf_fitted = False
            self._corpus_cache: list[str] = []
            self._model = "tfidf"
            self._loaded = True
            return

        if self.config.provider == "sentence-transformers":
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(
                    "Loading sentence-transformers model: %s", self.config.model_name
                )
                self._model = SentenceTransformer(
                    self.config.model_name,
                    device=self.config.device,
                )
                self._loaded = True
                logger.info("Model loaded successfully ✓")
            except ImportError:
                raise ImportError(
                    "sentence-transformers required: pip install sentence-transformers"
                )

        elif self.config.provider == "openai":
            try:
                import openai
                if self.config.openai_api_key:
                    openai.api_key = self.config.openai_api_key
                self._model = openai
                self._loaded = True
                logger.info("OpenAI embeddings configured: %s", self.config.openai_model)
            except ImportError:
                raise ImportError("openai required: pip install openai")

        else:
            raise ValueError(f"Unknown embedding provider: {self.config.provider}")

    @property
    def vector_size(self) -> int:
        return self.config.vector_size

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Vectoriser une liste de textes.

        Returns:
            list[list[float]] — une liste de vecteurs (pas numpy pour compat JSON)
        """
        if not texts:
            return []

        self._load_model()

        if self.config.provider == "tfidf-local":
            import numpy as np
            if not self._tfidf_fitted:
                # Premier appel: fit sur ces textes
                tfidf_matrix = self._tfidf.fit_transform(texts)
                n_comp = min(
                    max(1, tfidf_matrix.shape[0] - 1),
                    max(1, tfidf_matrix.shape[1] - 1),
                    self.config.vector_size,
                )
                self._svd.n_components = n_comp
                self._svd.fit(tfidf_matrix)
                self._tfidf_fitted = True
                self._corpus_cache.extend(texts)
                vectors_raw = self._svd.transform(tfidf_matrix)
            else:
                # Appels suivants: transformer dans l'espace appris
                # Si nouvelles données, re-fit pour élargir le vocabulaire
                new_texts = [t for t in texts if t not in self._corpus_cache]
                if new_texts:
                    self._corpus_cache.extend(new_texts)
                    full_corpus = self._corpus_cache
                    tfidf_all = self._tfidf.fit_transform(full_corpus)
                    n_comp = min(
                        max(1, tfidf_all.shape[0] - 1),
                        max(1, tfidf_all.shape[1] - 1),
                        self.config.vector_size,
                    )
                    self._svd.n_components = n_comp
                    self._svd.fit(tfidf_all)
                tfidf_matrix = self._tfidf.transform(texts)
                vectors_raw = self._svd.transform(tfidf_matrix)

            # Normaliser L2
            norms = np.linalg.norm(vectors_raw, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            vectors = vectors_raw / norms

            # Padder si nécessaire
            if vectors.shape[1] < self.config.vector_size:
                pad = np.zeros((vectors.shape[0], self.config.vector_size - vectors.shape[1]))
                vectors = np.concatenate([vectors, pad], axis=1)

            return [v.tolist() for v in vectors]

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

        elif self.config.provider == "openai":
            import openai
            all_vectors: list[list[float]] = []
            # OpenAI: batch par 100 max
            batch_size = min(self.config.batch_size, 100)
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                response = openai.embeddings.create(
                    input=batch,
                    model=self.config.openai_model,
                )
                for item in response.data:
                    all_vectors.append(item.embedding)
            return all_vectors

        raise ValueError(f"Unknown provider: {self.config.provider}")

    def embed_query(self, query: str) -> list[float]:
        """Vectoriser une requête de recherche."""
        results = self.embed_texts([query])
        return results[0] if results else []

    def embed_texts_batched(
        self, texts: list[str], batch_size: Optional[int] = None
    ) -> list[list[float]]:
        """
        Vectoriser en batches avec logging de progression.
        Utile pour les gros volumes.
        """
        bs = batch_size or self.config.batch_size
        all_vectors: list[list[float]] = []

        for i in range(0, len(texts), bs):
            batch = texts[i : i + bs]
            logger.debug(
                "Embedding batch %d/%d (%d texts)",
                i // bs + 1, (len(texts) + bs - 1) // bs, len(batch),
            )
            vectors = self.embed_texts(batch)
            all_vectors.extend(vectors)

        return all_vectors

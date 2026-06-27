"""
civitas.ingestion.qdrant.client
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Client Qdrant haut niveau pour le système d'ingestion CIVITAS.

Responsabilités:
  · Connexion et health-check Qdrant
  · Création/vérification de collections
  · Upsert de points en batch
  · Suppression de points (pour la mise à jour d'un fichier modifié)
  · Recherche sémantique (pour les tests et la validation)

Chaque point Qdrant stocke dans son payload:
  - file_path       : chemin absolu du fichier source
  - relative_path   : chemin relatif depuis la racine du scan
  - filename        : nom du fichier
  - extension       : extension du fichier
  - collection      : nom de la collection
  - chunk_index     : index du chunk dans le document
  - chunk_text      : texte du chunk
  - domain          : domaine métier
  - tags            : tags associés
  - depth           : profondeur dans l'arborescence
  - file_size_bytes : taille du fichier source
  - ingested_at     : timestamp d'ingestion (UTC ISO-8601)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from civitas.ingestion.qdrant.config import CollectionConfig, QdrantIngestionConfig

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    """Retourne l'heure UTC courante en ISO-8601 (aware, sans utcnow deprecated)."""
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────
#  QDRANT POINT
# ─────────────────────────────────────────────────────────────

@dataclass
class QdrantPoint:
    """Un point à upserter dans Qdrant."""
    id: str                     # UUID string unique
    vector: list[float]         # Vecteur d'embedding
    payload: dict[str, Any]     # Métadonnées complètes

    @classmethod
    def build(
        cls,
        vector: list[float],
        chunk_text: str,
        chunk_index: int,
        file_path: str,
        relative_path: str,
        filename: str,
        extension: str,
        collection: str,
        domain: str = "",
        tags: Optional[list[str]] = None,       # FIX: pas de mutable default
        depth: int = 0,
        file_size_bytes: int = 0,
        extra_payload: Optional[dict[str, Any]] = None,  # FIX: pas de mutable default
    ) -> "QdrantPoint":
        return cls(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "file_path": file_path,
                "relative_path": relative_path,
                "filename": filename,
                "extension": extension,
                "collection": collection,
                "chunk_index": chunk_index,
                "chunk_text": chunk_text,
                "domain": domain,
                "tags": tags if tags is not None else [],
                "depth": depth,
                "file_size_bytes": file_size_bytes,
                "ingested_at": _utcnow_iso(),   # FIX: datetime.utcnow() → timezone-aware
                **(extra_payload or {}),
            },
        )


@dataclass
class SearchResult:
    """Résultat d'une recherche sémantique."""
    point_id: str
    score: float
    file_path: str
    relative_path: str
    filename: str
    collection: str
    chunk_text: str
    chunk_index: int
    domain: str
    payload: dict[str, Any]

    def __repr__(self) -> str:
        return (
            f"SearchResult(score={self.score:.4f}, "
            f"file='{self.relative_path}', chunk={self.chunk_index})"
        )


# ─────────────────────────────────────────────────────────────
#  CIVITAS QDRANT CLIENT
# ─────────────────────────────────────────────────────────────

class CivitasQdrantClient:
    """
    Client Qdrant haut niveau pour CIVITAS.

    Usage:
        client = CivitasQdrantClient.from_config(config)
        client.ensure_collection("ansible_docs", vector_size=384)
        client.upsert_points("ansible_docs", points)
        results = client.search("ansible_docs", query_vector, top_k=5)
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        timeout: int = 60,
        in_memory: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.api_key = api_key
        self.url = url
        self.timeout = timeout
        self.in_memory = in_memory
        self._client = None

    @classmethod
    def from_config(cls, config: "QdrantIngestionConfig") -> "CivitasQdrantClient":
        return cls(
            host=config.qdrant_host,
            port=config.qdrant_port,
            api_key=config.qdrant_api_key,
            url=config.qdrant_url,
            in_memory=getattr(config, "qdrant_in_memory", False),
        )

    @staticmethod
    def _url_is_cloud(url: str) -> bool:
        """
        Retourne True uniquement si l'URL est une URL Qdrant Cloud distante (TLS requis).

        Une URL est CLOUD (TLS) si elle commence par https:// ET ne cible pas
        localhost / 127.x / ::1.

        Cas déclenchant SSL WRONG_VERSION_NUMBER à éviter absolument :
          - QDRANT_URL=https://localhost:6333   → LOCAL, pas de TLS
          - QDRANT_URL=http://localhost:6333    → LOCAL, pas de TLS
          - url="" ou url=None                 → LOCAL host:port
          - url=https://xxxx.cloud.qdrant.io   → CLOUD, TLS
        """
        if not url:
            return False
        u = url.lower()
        if not u.startswith("https://"):
            return False
        # https:// mais vers un hôte local → pas de TLS
        local_patterns = ("localhost", "127.", "::1", "0.0.0.0")
        return not any(p in u for p in local_patterns)

    def _get_client(self):
        """
        Lazy-init du client Qdrant.

        Priorité de connexion :
          1. in_memory=True              → mode test, aucun serveur requis
          2. url pointe vers Qdrant Cloud → connexion TLS (https://xxx.cloud.qdrant.io)
          3. Tous les autres cas          → connexion HTTP plain via host:port

        Le cas 3 couvre :
          - QDRANT_URL non défini (cas standard local/Docker)
          - QDRANT_URL=http://localhost:6333 (URL locale explicite)
          - QDRANT_URL=https://localhost:6333 (mauvaise config → on ignore le TLS)

        Cela évite l'erreur [SSL: WRONG_VERSION_NUMBER] quand QDRANT_URL est
        défini avec une URL locale et que Qdrant tourne sans TLS.
        """
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient
        except ImportError:
            raise ImportError("qdrant-client required: pip install qdrant-client")

        if self.in_memory:
            self._client = QdrantClient(":memory:")
            logger.info("Using Qdrant in-memory mode (dev/test)")

        elif self._url_is_cloud(self.url):
            # Qdrant Cloud — connexion TLS obligatoire
            self._client = QdrantClient(
                url=self.url,
                api_key=self.api_key,
                timeout=self.timeout,
            )
            logger.info("Connected to Qdrant Cloud: %s", self.url)

        else:
            # Local / Docker — HTTP plain, prefer_grpc=False pour éviter tout TLS
            api_key = self.api_key if self.api_key else None
            self._client = QdrantClient(
                host=self.host,
                port=self.port,
                api_key=api_key,
                timeout=self.timeout,
                prefer_grpc=False,
            )
            logger.info("Connected to Qdrant: http://%s:%d", self.host, self.port)

        return self._client

    # ── Health ─────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Vérifier que Qdrant est accessible."""
        try:
            client = self._get_client()
            client.get_collections()
            return True
        except Exception as e:
            logger.error("Qdrant health check failed: %s", e)
            return False

    def get_server_info(self) -> dict:
        """Info sur le serveur Qdrant."""
        try:
            client = self._get_client()
            collections = client.get_collections()   # FIX: ligne fantôme supprimée
            return {
                "status": "ok",
                "collections_count": len(collections.collections),
                "collections": [c.name for c in collections.collections],
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── Collections ────────────────────────────────────────────

    def list_collections(self) -> list[str]:
        """Liste toutes les collections Qdrant existantes."""
        client = self._get_client()
        result = client.get_collections()
        return [c.name for c in result.collections]

    def collection_exists(self, name: str) -> bool:
        """Vérifie si une collection existe via l'API native (plus efficace que list)."""
        try:
            # FIX: utiliser collection_exists natif du client plutôt que list_collections()
            return self._get_client().collection_exists(name)
        except Exception:
            return name in self.list_collections()

    def ensure_collection(
        self,
        name: str,
        vector_size: int,
        collection_config: Optional["CollectionConfig"] = None,
        distance: str = "Cosine",
    ) -> bool:
        """
        Créer une collection si elle n'existe pas.
        Si elle existe déjà, ne fait rien (idempotent).

        Returns:
            True si créée, False si déjà existante.
        """
        from qdrant_client.models import (
            Distance, VectorParams, HnswConfigDiff, OptimizersConfigDiff,
        )

        distance_map = {
            "Cosine": Distance.COSINE,
            "Euclid": Distance.EUCLID,
            "Dot": Distance.DOT,
        }

        if self.collection_exists(name):
            logger.debug("Collection '%s' already exists", name)
            return False

        cfg_distance = distance
        on_disk_payload = True
        # FIX: replication_factor maintenant transmis à Qdrant
        replication_factor = 1
        if collection_config:
            cfg_distance = collection_config.distance
            on_disk_payload = collection_config.on_disk_payload
            replication_factor = getattr(collection_config, "replication_factor", 1)

        client = self._get_client()
        create_kwargs: dict[str, Any] = dict(
            collection_name=name,
            vectors_config=VectorParams(
                size=vector_size,
                distance=distance_map.get(cfg_distance, Distance.COSINE),
            ),
            hnsw_config=HnswConfigDiff(
                m=16,
                ef_construct=100,
                full_scan_threshold=10000,
                on_disk=False,
            ),
            optimizers_config=OptimizersConfigDiff(
                indexing_threshold=20000,
            ),
            on_disk_payload=on_disk_payload,
        )
        # replication_factor > 1 uniquement sur un cluster multi-nœuds
        if replication_factor > 1:
            create_kwargs["replication_factor"] = replication_factor

        client.create_collection(**create_kwargs)
        logger.info(
            "Collection '%s' created (dim=%d, dist=%s)", name, vector_size, cfg_distance
        )
        return True

    def get_collection_info(self, name: str) -> dict:
        """Info détaillée sur une collection."""
        try:
            client = self._get_client()
            info = client.get_collection(name)
            return {
                "name": name,
                "status": str(info.status),
                "points_count": info.points_count,
                "vectors_count": info.vectors_count,
                "indexed_vectors_count": info.indexed_vectors_count,
                "vector_size": info.config.params.vectors.size,
                "distance": str(info.config.params.vectors.distance),
            }
        except Exception as e:
            return {"name": name, "error": str(e)}

    def delete_collection(self, name: str) -> bool:
        """Supprimer une collection (irréversible)."""
        if not self.collection_exists(name):
            return False
        self._get_client().delete_collection(name)
        logger.info("Collection '%s' deleted", name)
        return True

    # ── Points ─────────────────────────────────────────────────

    def upsert_points(
        self,
        collection_name: str,
        points: list[QdrantPoint],
        batch_size: int = 50,
    ) -> int:
        """
        Upsert des points en batch.
        Retourne le nombre de points effectivement upsertés.
        """
        if not points:
            return 0

        from qdrant_client.models import PointStruct

        client = self._get_client()
        total = 0

        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            qdrant_points = [
                PointStruct(id=p.id, vector=p.vector, payload=p.payload)
                for p in batch
            ]
            client.upsert(
                collection_name=collection_name,
                points=qdrant_points,
                wait=True,
            )
            total += len(batch)
            logger.debug(
                "Upserted batch %d/%d (%d points) → '%s'",
                i // batch_size + 1,
                (len(points) + batch_size - 1) // batch_size,
                len(batch),
                collection_name,
            )

        return total

    def delete_points_by_ids(
        self,
        collection_name: str,
        point_ids: list[str],
    ) -> int:
        """Supprimer des points par leurs IDs (ex: mise à jour d'un fichier modifié)."""
        if not point_ids or not self.collection_exists(collection_name):
            return 0

        from qdrant_client.models import PointIdsList

        self._get_client().delete(
            collection_name=collection_name,
            points_selector=PointIdsList(points=point_ids),
            wait=True,
        )
        logger.debug("Deleted %d points from '%s'", len(point_ids), collection_name)
        return len(point_ids)

    def delete_points_by_file(
        self,
        collection_name: str,
        file_path: str,
    ) -> int:
        """
        Supprimer tous les points d'un fichier via filtre payload (file_path).
        Utile pour forcer une réingestion propre sans connaître les IDs.
        """
        if not self.collection_exists(collection_name):
            return 0

        from qdrant_client.models import Filter, FieldCondition, MatchValue

        self._get_client().delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]
            ),
            wait=True,
        )
        logger.debug("Deleted points for file '%s' from '%s'", file_path, collection_name)
        # Qdrant ne retourne pas le count dans ce cas — retourner -1 pour signaler l'exécution
        return -1

    # ── Search ──────────────────────────────────────────────────

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        top_k: int = 10,
        score_threshold: float = 0.0,
        filter_domain: Optional[str] = None,
        filter_tags: Optional[list[str]] = None,
        filter_extension: Optional[str] = None,
    ) -> list[SearchResult]:
        """
        Recherche sémantique dans une collection.

        Args:
            collection_name:  Collection à interroger
            query_vector:     Vecteur de la requête (même dimension que les chunks)
            top_k:            Nombre de résultats à retourner
            score_threshold:  Score minimum (0.0 = pas de filtre)
            filter_domain:    Filtrer par domaine métier (payload.domain)
            filter_tags:      Filtrer par tags (OR — payload.tags contient au moins un)
            filter_extension: Filtrer par extension de fichier (payload.extension)
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

        must_conditions = []
        if filter_domain:
            must_conditions.append(
                FieldCondition(key="domain", match=MatchValue(value=filter_domain))
            )
        if filter_extension:
            must_conditions.append(
                FieldCondition(key="extension", match=MatchValue(value=filter_extension))
            )
        if filter_tags:
            must_conditions.append(
                FieldCondition(key="tags", match=MatchAny(any=filter_tags))
            )

        search_filter = Filter(must=must_conditions) if must_conditions else None

        response = self._get_client().query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=top_k,
            score_threshold=score_threshold if score_threshold > 0.0 else None,
            query_filter=search_filter,
            with_payload=True,
        )

        return [
            SearchResult(
                point_id=str(hit.id),
                score=hit.score,
                file_path=hit.payload.get("file_path", ""),
                relative_path=hit.payload.get("relative_path", ""),
                filename=hit.payload.get("filename", ""),
                collection=hit.payload.get("collection", collection_name),
                chunk_text=hit.payload.get("chunk_text", ""),
                chunk_index=hit.payload.get("chunk_index", 0),
                domain=hit.payload.get("domain", ""),
                payload=hit.payload,
            )
            for hit in response.points
        ]

    def search_across_collections(
        self,
        query_vector: list[float],
        collection_names: list[str],
        top_k: int = 10,
        score_threshold: float = 0.0,
    ) -> list[SearchResult]:
        """
        Recherche dans plusieurs collections simultanément.

        FIX: récupère la liste des collections existantes une seule fois
        pour éviter N appels à collection_exists() (chacun appelle list_collections).
        Les résultats sont fusionnés et re-triés par score décroissant.
        """
        # FIX: un seul appel list_collections au lieu de N appels collection_exists
        existing = set(self.list_collections())
        all_results: list[SearchResult] = []

        for collection in collection_names:
            if collection not in existing:
                continue
            try:
                results = self.search(
                    collection_name=collection,
                    query_vector=query_vector,
                    top_k=top_k,
                    score_threshold=score_threshold,
                )
                all_results.extend(results)
            except Exception as e:
                logger.warning("Search failed in collection '%s': %s", collection, e)

        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:top_k]

    def count_points(self, collection_name: str) -> int:
        """Nombre de points dans une collection."""
        try:
            info = self.get_collection_info(collection_name)
            return info.get("points_count", 0) or 0
        except Exception:
            return 0

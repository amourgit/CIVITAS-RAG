"""
civitas.ingestion.qdrant.tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Système de tracking pour l'ingestion incrémentale.

Stocke l'état de chaque fichier ingéré dans une base SQLite locale,
permettant:
  · La déduplication stricte (un fichier n'est jamais réindexé si inchangé)
  · La détection des fichiers modifiés (via hash SHA-256 + mtime)
  · L'historique complet des ingestions
  · Le nettoyage sélectif (reset par collection ou global)

Structure de la DB:
  Table ingested_files:
    - file_path      : chemin absolu du fichier
    - collection     : nom de la collection Qdrant
    - file_hash      : SHA-256 du contenu
    - mtime          : modification time (float)
    - file_size      : taille en octets
    - ingested_at    : timestamp UTC ISO-8601 d'ingestion
    - point_ids      : IDs des points Qdrant créés (JSON list)
    - chunks_count   : nombre de chunks créés
    - status         : success | failed | skipped
    - error_msg      : message d'erreur si échec
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    """Retourne l'heure UTC courante en ISO-8601 (aware, sans utcnow deprecated)."""
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────
#  RECORD
# ─────────────────────────────────────────────────────────────

class IngestionRecord:
    """Représente l'état d'ingestion d'un fichier."""

    __slots__ = (
        "file_path", "collection", "file_hash", "mtime",
        "file_size", "ingested_at", "point_ids", "chunks_count",
        "status", "error_msg",
    )

    def __init__(
        self,
        file_path: str,
        collection: str,
        file_hash: str,
        mtime: float,
        file_size: int,
        ingested_at: str,
        point_ids: list[str],
        chunks_count: int,
        status: str,
        error_msg: Optional[str] = None,
    ) -> None:
        self.file_path = file_path
        self.collection = collection
        self.file_hash = file_hash
        self.mtime = mtime
        self.file_size = file_size
        self.ingested_at = ingested_at
        self.point_ids = point_ids
        self.chunks_count = chunks_count
        self.status = status
        self.error_msg = error_msg

    @property
    def is_success(self) -> bool:
        return self.status == "success"


# ─────────────────────────────────────────────────────────────
#  TRACKER
# ─────────────────────────────────────────────────────────────

class IngestionTracker:
    """
    Tracker SQLite pour l'ingestion incrémentale.

    Thread-safe via WAL mode + connexions par opération.
    La base est créée automatiquement si elle n'existe pas.

    Usage:
        tracker = IngestionTracker(".civitas_tracker.db")
        should, reason = tracker.should_ingest(file_path, collection)
        if should:
            # Ingérer...
            tracker.mark_success(file_path, collection, point_ids, chunks)
        else:
            # Fichier déjà ingéré et non modifié — skip
    """

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS ingested_files (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path    TEXT    NOT NULL,
        collection   TEXT    NOT NULL,
        file_hash    TEXT    NOT NULL DEFAULT '',
        mtime        REAL    NOT NULL DEFAULT 0.0,
        file_size    INTEGER NOT NULL DEFAULT 0,
        ingested_at  TEXT    NOT NULL,
        point_ids    TEXT    NOT NULL DEFAULT '[]',
        chunks_count INTEGER NOT NULL DEFAULT 0,
        status       TEXT    NOT NULL DEFAULT 'success'
                              CHECK(status IN ('success','failed','skipped')),
        error_msg    TEXT,
        UNIQUE (file_path, collection)
    );
    CREATE INDEX IF NOT EXISTS idx_ingested_collection ON ingested_files(collection);
    CREATE INDEX IF NOT EXISTS idx_ingested_file_path  ON ingested_files(file_path);
    CREATE INDEX IF NOT EXISTS idx_ingested_status     ON ingested_files(status);
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._ensure_schema()
        logger.debug("IngestionTracker initialized: %s", self.db_path)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        """Créer les tables et index si absents (idempotent)."""
        with self._conn() as conn:
            conn.executescript(self._SCHEMA_SQL)

    # ── Déduplication ─────────────────────────────────────────

    @staticmethod
    def compute_file_hash(file_path: str | Path) -> str:
        """Calcule le SHA-256 d'un fichier en chunks de 512KB."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(524288), b""):
                h.update(chunk)
        return h.hexdigest()

    def get_record(
        self, file_path: str, collection: str
    ) -> Optional[IngestionRecord]:
        """Retourne l'enregistrement d'ingestion d'un fichier, si existant."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ingested_files WHERE file_path=? AND collection=?",
                (str(file_path), collection),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_record(row)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> IngestionRecord:
        return IngestionRecord(
            file_path=row["file_path"],
            collection=row["collection"],
            file_hash=row["file_hash"],
            mtime=row["mtime"],
            file_size=row["file_size"],
            ingested_at=row["ingested_at"],
            point_ids=json.loads(row["point_ids"]),
            chunks_count=row["chunks_count"],
            status=row["status"],
            error_msg=row["error_msg"],
        )

    def should_ingest(
        self,
        file_path: str | Path,
        collection: str,
        skip_existing: bool = True,
    ) -> tuple[bool, str]:
        """
        Détermine si un fichier doit être ingéré.

        Returns:
            (should_process, reason) où reason ∈:
              "new"           → jamais vu, à ingérer
              "modified"      → modifié depuis la dernière ingestion, à réingérer
              "failed"        → précédente tentative échouée, à réessayer
              "force_reingest"→ skip_existing=False, toujours traiter
              "unchanged"     → déjà ingéré avec succès et non modifié, skip
              "not_found"     → fichier absent du disque, skip
        """
        if not skip_existing:
            return True, "force_reingest"

        file_path = Path(file_path)
        if not file_path.exists():
            return False, "not_found"

        record = self.get_record(str(file_path), collection)
        if record is None:
            return True, "new"

        if record.status == "failed":
            return True, "failed"

        # Vérification rapide mtime + taille avant de hacher
        try:
            stat = file_path.stat()
            if stat.st_mtime != record.mtime or stat.st_size != record.file_size:
                current_hash = self.compute_file_hash(file_path)
                if current_hash != record.file_hash:
                    return True, "modified"
        except OSError as e:
            logger.warning("Cannot stat file %s: %s", file_path, e)
            return True, "stat_error"

        return False, "unchanged"

    # ── Écriture ──────────────────────────────────────────────

    def mark_success(
        self,
        file_path: str | Path,
        collection: str,
        point_ids: list[str],
        chunks_count: int,
        file_hash: Optional[str] = None,
    ) -> None:
        """Enregistrer un fichier comme ingéré avec succès (INSERT OR REPLACE)."""
        file_path = Path(file_path)
        stat = file_path.stat()
        h = file_hash or self.compute_file_hash(file_path)

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO ingested_files
                    (file_path, collection, file_hash, mtime, file_size,
                     ingested_at, point_ids, chunks_count, status, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'success', NULL)
                ON CONFLICT(file_path, collection) DO UPDATE SET
                    file_hash    = excluded.file_hash,
                    mtime        = excluded.mtime,
                    file_size    = excluded.file_size,
                    ingested_at  = excluded.ingested_at,
                    point_ids    = excluded.point_ids,
                    chunks_count = excluded.chunks_count,
                    status       = 'success',
                    error_msg    = NULL
                """,
                (
                    str(file_path),
                    collection,
                    h,
                    stat.st_mtime,
                    stat.st_size,
                    _utcnow_iso(),              # FIX: UTC-aware
                    json.dumps(point_ids),
                    chunks_count,
                ),
            )

    def mark_failed(
        self,
        file_path: str | Path,
        collection: str,
        error_msg: str,
    ) -> None:
        """Enregistrer un fichier comme échoué pour réessai au prochain run."""
        file_path = Path(file_path)
        try:
            stat = file_path.stat()
            mtime, size = stat.st_mtime, stat.st_size
        except OSError:
            mtime, size = 0.0, 0

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO ingested_files
                    (file_path, collection, file_hash, mtime, file_size,
                     ingested_at, point_ids, chunks_count, status, error_msg)
                VALUES (?, ?, '', ?, ?, ?, '[]', 0, 'failed', ?)
                ON CONFLICT(file_path, collection) DO UPDATE SET
                    mtime       = excluded.mtime,
                    file_size   = excluded.file_size,
                    ingested_at = excluded.ingested_at,
                    status      = 'failed',
                    error_msg   = excluded.error_msg
                """,
                (
                    str(file_path),
                    collection,
                    mtime,
                    size,
                    _utcnow_iso(),              # FIX: UTC-aware
                    error_msg[:2000],
                ),
            )

    # ── Lecture / Stats ───────────────────────────────────────

    def list_collection(self, collection: str) -> list[IngestionRecord]:
        """Liste tous les fichiers ingérés dans une collection."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ingested_files WHERE collection=? ORDER BY ingested_at DESC",
                (collection,),
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def stats(self, collection: Optional[str] = None) -> dict:
        """Retourne des statistiques agrégées sur l'ingestion."""
        with self._conn() as conn:
            where = "WHERE collection=?" if collection else ""
            params: tuple = (collection,) if collection else ()

            row = conn.execute(
                f"""
                SELECT
                    COUNT(*)                                              AS total,
                    SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)   AS succeeded,
                    SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END)   AS failed,
                    SUM(chunks_count)                                     AS total_chunks,
                    SUM(file_size)                                        AS total_bytes,
                    MIN(ingested_at)                                      AS first_ingestion,
                    MAX(ingested_at)                                      AS last_ingestion
                FROM ingested_files {where}
                """,
                params,
            ).fetchone()

            collections_count = conn.execute(
                "SELECT COUNT(DISTINCT collection) FROM ingested_files"
            ).fetchone()[0]

            return {
                "total_files":       row["total"]        or 0,
                "succeeded":         row["succeeded"]    or 0,
                "failed":            row["failed"]       or 0,
                "total_chunks":      row["total_chunks"] or 0,
                "total_bytes":       row["total_bytes"]  or 0,
                "collections_count": collections_count,
                "first_ingestion":   row["first_ingestion"],
                "last_ingestion":    row["last_ingestion"],
            }

    def list_collections(self) -> list[dict]:
        """Liste toutes les collections connues du tracker avec leurs stats."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    collection,
                    COUNT(*)          AS files,
                    SUM(chunks_count) AS chunks,
                    SUM(file_size)    AS bytes,
                    MAX(ingested_at)  AS last_ingestion
                FROM ingested_files
                WHERE status = 'success'
                GROUP BY collection
                ORDER BY collection
                """
            ).fetchall()
            return [
                {
                    "collection":     r["collection"],
                    "files":          r["files"],
                    "chunks":         r["chunks"]         or 0,
                    "bytes":          r["bytes"]          or 0,
                    "last_ingestion": r["last_ingestion"],
                }
                for r in rows
            ]

    # ── Nettoyage ─────────────────────────────────────────────

    def reset_collection(self, collection: str) -> int:
        """Supprimer tous les enregistrements d'une collection (force réingestion)."""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM ingested_files WHERE collection=?", (collection,)
            )
        logger.info(
            "Tracker reset for collection '%s': %d records deleted",
            collection, cursor.rowcount,
        )
        return cursor.rowcount

    def reset_all(self) -> int:
        """Supprimer TOUS les enregistrements (réingestion totale)."""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM ingested_files")
        logger.info("Tracker fully reset: %d records deleted", cursor.rowcount)
        return cursor.rowcount

    def remove_file(self, file_path: str, collection: str) -> bool:
        """Supprimer l'enregistrement d'un fichier spécifique."""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM ingested_files WHERE file_path=? AND collection=?",
                (file_path, collection),
            )
            return cursor.rowcount > 0

    def get_point_ids(self, file_path: str, collection: str) -> list[str]:
        """Récupérer les IDs Qdrant associés à un fichier (pour suppression ciblée)."""
        record = self.get_record(file_path, collection)
        return record.point_ids if record else []

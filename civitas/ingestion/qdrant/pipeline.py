"""
civitas.ingestion.qdrant.pipeline
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Pipeline d'ingestion principal pour Qdrant.

Flux complet:
  1. SCAN      — découverte récursive des fichiers (FileScanner)
  2. FILTER    — déduplication via IngestionTracker (skip si inchangé)
  3. READ      — lecture robuste du contenu (text/yaml/pdf/docx...)
  4. CHUNK     — découpage en chunks chevauchants (TextChunker)
  5. EMBED     — vectorisation (DocumentEmbedder)
  6. UPSERT    — insertion batch dans Qdrant (CivitasQdrantClient)
  7. TRACK     — enregistrement du résultat dans SQLite

Caractéristiques:
  · Incrémental  — traite uniquement les fichiers nouveaux ou modifiés
  · Idempotent   — relançable à volonté, jamais de doublons
  · Paramétrable — collection, chemin, extensions, chunks, domain, tags...
  · Dry-run      — analyse complète sans écrire dans Qdrant
  · Rapport      — résumé détaillé après chaque run (Rich ou plain)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from civitas.ingestion.qdrant.client import CivitasQdrantClient, QdrantPoint
from civitas.ingestion.qdrant.config import QdrantIngestionConfig, ScanConfig
from civitas.ingestion.qdrant.embedder import DocumentEmbedder, TextChunker
from civitas.ingestion.qdrant.scanner import DiscoveredFile, FileScanner
from civitas.ingestion.qdrant.tracker import IngestionTracker

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  RESULT TYPES
# ─────────────────────────────────────────────────────────────

@dataclass
class FileIngestionResult:
    """Résultat d'ingestion d'un seul fichier."""
    file_path: str
    relative_path: str
    collection: str
    # Statuts: new | modified | failed | skipped | dry_run
    status: str = "pending"
    chunks_count: int = 0
    point_ids: list[str] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: int = 0

    @property
    def is_success(self) -> bool:
        return self.status in ("new", "modified", "dry_run")

    @property
    def is_skipped(self) -> bool:
        return self.status == "skipped"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"


@dataclass
class ScanIngestionReport:
    """Rapport complet d'un run d'ingestion."""
    scan_config: ScanConfig
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)  # FIX: UTC-aware
    )
    finished_at: Optional[datetime] = None

    # Compteurs globaux
    total_discovered: int = 0
    total_new: int = 0
    total_modified: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    total_chunks: int = 0
    total_points: int = 0
    total_duration_ms: int = 0

    # Détails par fichier
    results: list[FileIngestionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.total_new + self.total_modified + self.total_failed

    @property
    def success_rate(self) -> float:
        """Taux de succès sur les fichiers effectivement traités (hors skips et dry_run)."""
        # FIX: dry_run → total_new est incrémenté, mais pas total_points
        # la success_rate doit compter correctement les dry_run comme succès
        attempted = self.total_new + self.total_modified + self.total_failed
        if attempted == 0:
            return 1.0
        return (self.total_new + self.total_modified) / attempted

    @property
    def duration_seconds(self) -> float:
        if self.finished_at and self.started_at:
            return (self.finished_at - self.started_at).total_seconds()
        return self.total_duration_ms / 1000

    def add_result(self, result: FileIngestionResult) -> None:
        self.results.append(result)
        self.total_duration_ms += result.duration_ms

        if result.status == "new":
            self.total_new += 1
            self.total_chunks += result.chunks_count
            self.total_points += len(result.point_ids)
        elif result.status == "modified":
            self.total_modified += 1
            self.total_chunks += result.chunks_count
            self.total_points += len(result.point_ids)
        elif result.status == "skipped":
            self.total_skipped += 1
        elif result.status == "failed":
            self.total_failed += 1
            if result.error:
                self.errors.append(f"{result.relative_path}: {result.error}")
        elif result.status == "dry_run":
            # FIX: dry_run compte comme "new" dans les stats de traitement
            # mais total_points reste 0 (rien écrit dans Qdrant)
            self.total_new += 1
            self.total_chunks += result.chunks_count

    def print_report(self) -> None:
        """Afficher le rapport dans le terminal (Rich si disponible, sinon plain)."""
        try:
            from rich.console import Console
            console = Console()
            _print_rich_report(self, console)
        except ImportError:
            _print_plain_report(self)


# ─────────────────────────────────────────────────────────────
#  FILE READER
# ─────────────────────────────────────────────────────────────

def _read_text(file_path: Path) -> Optional[str]:
    """
    Lire le contenu texte d'un fichier de façon robuste.

    Supporte: texte brut (toutes encodings), YAML, JSON, Terraform, Shell,
    Dockerfile, Jenkinsfile, Makefile, PDF (pypdf), DOCX (python-docx).
    Retourne None si le fichier est vide ou illisible.
    """
    ext = file_path.suffix.lower()

    # ── PDF ───────────────────────────────────────────────────
    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(str(file_path))
            pages  = [page.extract_text() or "" for page in reader.pages]
            text   = "\n\n".join(t for t in pages if t.strip())
            return text or None
        except Exception as e:
            logger.warning("PDF read failed %s: %s", file_path.name, e)
            return None

    # ── DOCX / DOC ────────────────────────────────────────────
    if ext in (".docx", ".doc"):
        try:
            import docx
            doc  = docx.Document(str(file_path))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return text or None
        except Exception as e:
            logger.warning("DOCX read failed %s: %s", file_path.name, e)
            return None

    # ── Texte brut (toutes encodings) ─────────────────────────
    raw = file_path.read_bytes()
    if not raw:
        return None

    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # Fallback ultime : latin-1 avec remplacement des caractères illisibles
    return raw.decode("latin-1", errors="replace")


# ─────────────────────────────────────────────────────────────
#  PIPELINE
# ─────────────────────────────────────────────────────────────

class QdrantIngestionPipeline:
    """
    Pipeline d'ingestion Qdrant paramétrable et idempotent.

    Usage minimal:
        config = QdrantIngestionConfig()
        pipeline = QdrantIngestionPipeline(config)
        report = pipeline.ingest(ScanConfig(
            source_path="/data/documents/ansible",
            collection_name="ansible_docs",
            domain="devops",
        ))
        report.print_report()

    Multi-scan:
        pipeline.ingest_many([
            ScanConfig(source_path="/data/ansible",   collection_name="ansible_docs"),
            ScanConfig(source_path="/data/terraform", collection_name="terraform_docs"),
            ScanConfig(source_path="/data/cicd",      collection_name="cicd_docs"),
        ])
    """

    def __init__(self, config: QdrantIngestionConfig) -> None:
        self.config  = config
        self.tracker = IngestionTracker(config.tracker_db_path)
        self.qdrant  = CivitasQdrantClient.from_config(config)
        self._embedder: Optional[DocumentEmbedder] = None

    @property
    def embedder(self) -> DocumentEmbedder:
        """Lazy-init de l'embedder singleton (partagé entre ingestion et search)."""
        if self._embedder is None:
            self._embedder = DocumentEmbedder.get_or_create(self.config.embedding)
        return self._embedder

    # ── Main Entry Points ──────────────────────────────────────

    def ingest(self, scan: ScanConfig) -> ScanIngestionReport:
        """
        Lancer un scan d'ingestion complet.

        Args:
            scan: ScanConfig — paramètres complets du scan.

        Returns:
            ScanIngestionReport — rapport détaillé du run.
        """
        report  = ScanIngestionReport(scan_config=scan)
        t_start = time.monotonic()

        logger.info(
            "═══ Ingestion START ═══  collection='%s'  path='%s'  dry_run=%s",
            scan.collection_name, scan.source_path, scan.dry_run,
        )

        # 1. Health-check Qdrant (sauf dry-run et in-memory)
        if not scan.dry_run and not getattr(self.config, "qdrant_in_memory", False):
            if not self.qdrant.health_check():
                raise ConnectionError(
                    f"Cannot connect to Qdrant at "
                    f"{self.config.qdrant_host}:{self.config.qdrant_port}.\n"
                    "Start Qdrant with:\n"
                    "  docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant"
                )

        # 2. Résoudre chunk_size/overlap (scan > collection > global)
        #    FIX: utiliser None-check plutôt que falsiness (0 est une valeur valide)
        col_config    = self.config.get_collection(scan.collection_name)
        chunk_size    = scan.chunk_size    if scan.chunk_size    is not None else col_config.chunk_size
        chunk_overlap = scan.chunk_overlap if scan.chunk_overlap is not None else col_config.chunk_overlap
        chunker       = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

        # 3. Créer la collection si nécessaire (skip en dry-run)
        if not scan.dry_run:
            self.qdrant.ensure_collection(
                name=scan.collection_name,
                vector_size=self.config.embedding.vector_size,
                collection_config=col_config,
            )

        # 4. Scanner les fichiers
        scanner = FileScanner(
            allowed_extensions=self.config.effective_extensions(scan),
            excluded_patterns=scan.excluded_patterns,
            max_file_size_mb=scan.max_file_size_mb,
            recursive=scan.recursive,
        )
        scan_result              = scanner.scan(scan.source_path)
        report.total_discovered  = scan_result.total_discovered

        logger.info(
            "Discovered %d files in '%s' (%d filtered out)",
            scan_result.total_discovered,
            scan.source_path,
            scan_result.total_skipped,
        )

        # 5. Traiter chaque fichier
        for discovered_file in scan_result.discovered:
            result = self._process_file(
                discovered_file=discovered_file,
                scan=scan,
                chunker=chunker,
                dry_run=scan.dry_run,
            )
            report.add_result(result)

        report.finished_at = datetime.now(timezone.utc)  # FIX: UTC-aware
        elapsed = time.monotonic() - t_start
        logger.info(
            "═══ Ingestion END ═══  new=%d  modified=%d  skipped=%d  "
            "failed=%d  chunks=%d  duration=%.1fs",
            report.total_new, report.total_modified,
            report.total_skipped, report.total_failed,
            report.total_chunks, elapsed,
        )
        return report

    def ingest_many(self, scans: list[ScanConfig]) -> list[ScanIngestionReport]:
        """Lancer plusieurs scans séquentiellement et retourner tous les rapports."""
        reports = []
        for i, scan in enumerate(scans, 1):
            logger.info(
                "─── Scan %d/%d: '%s' → '%s' ───",
                i, len(scans), scan.source_path, scan.collection_name,
            )
            reports.append(self.ingest(scan))
        return reports

    # ── File Processing ────────────────────────────────────────

    def _process_file(
        self,
        discovered_file: DiscoveredFile,
        scan: ScanConfig,
        chunker: TextChunker,
        dry_run: bool,
    ) -> FileIngestionResult:
        """Traiter un fichier unique à travers toutes les étapes du pipeline."""
        t0         = time.monotonic()
        file_path  = str(discovered_file.path)
        rel_path   = discovered_file.relative_path
        collection = scan.collection_name

        result = FileIngestionResult(
            file_path=file_path,
            relative_path=rel_path,
            collection=collection,
        )

        try:
            # ── Étape 1: Déduplication ────────────────────────
            should_process, reason = self.tracker.should_ingest(
                file_path, collection, skip_existing=scan.skip_existing
            )

            if not should_process:
                result.status = "skipped"
                logger.debug("SKIP [%s] %s (unchanged)", collection, rel_path)
                return result

            # ── Étape 2: Nettoyage des anciens points (fichier modifié) ──
            if reason == "modified" and not dry_run:
                old_ids = self.tracker.get_point_ids(file_path, collection)
                if old_ids:
                    self.qdrant.delete_points_by_ids(collection, old_ids)
                    logger.debug(
                        "Deleted %d stale points for modified file: %s",
                        len(old_ids), rel_path,
                    )

            # ── Étape 3: Lecture du contenu ───────────────────
            text = _read_text(discovered_file.path)
            if not text or not text.strip():
                logger.warning("Empty or unreadable content: %s", rel_path)
                result.status = "failed"
                result.error  = "Empty or unreadable content"
                if not dry_run:
                    self.tracker.mark_failed(file_path, collection, result.error)
                return result

            # ── Étape 4: Chunking ─────────────────────────────
            chunks = chunker.chunk_with_context(text, rel_path)
            if not chunks:
                result.status = "failed"
                result.error  = "No chunks produced from content"
                if not dry_run:
                    self.tracker.mark_failed(file_path, collection, result.error)
                return result

            result.chunks_count = len(chunks)

            # ── Dry-run: s'arrêter ici ────────────────────────
            if dry_run:
                result.status = "dry_run"
                logger.info(
                    "[DRY RUN] %s → %d chunks (would index into '%s')",
                    rel_path, len(chunks), collection,
                )
                return result

            # ── Étape 5: Embedding ────────────────────────────
            chunk_texts = [c.text for c in chunks]
            vectors     = self.embedder.embed_texts(chunk_texts)

            if len(vectors) != len(chunks):
                raise ValueError(
                    f"Embedding mismatch: got {len(vectors)} vectors "
                    f"for {len(chunks)} chunks in {rel_path}"
                )

            # ── Étape 6: Construire et upserter les points ────
            col_config = self.config.get_collection(collection)
            tags   = sorted(set(scan.tags + col_config.default_tags))
            domain = scan.domain or col_config.default_domain

            points = [
                QdrantPoint.build(
                    vector=vectors[i],
                    chunk_text=chunks[i].text,
                    chunk_index=chunks[i].chunk_index,
                    file_path=file_path,
                    relative_path=rel_path,
                    filename=discovered_file.filename,
                    extension=discovered_file.extension,
                    collection=collection,
                    domain=domain,
                    tags=tags,
                    depth=discovered_file.depth,
                    file_size_bytes=discovered_file.size_bytes,
                )
                for i in range(len(chunks))
            ]

            # FIX: variable 'upserted' supprimée — upsert_points retourne le count
            # mais on n'en a pas besoin ici (on utilise len(points))
            self.qdrant.upsert_points(
                collection_name=collection,
                points=points,
                batch_size=self.config.batch_size,
            )

            result.point_ids = [p.id for p in points]
            # FIX: statut = reason réel (new ou modified), jamais "pending"
            result.status    = reason if reason in ("new", "modified") else "new"

            # ── Étape 7: Tracker ──────────────────────────────
            self.tracker.mark_success(
                file_path=file_path,
                collection=collection,
                point_ids=result.point_ids,
                chunks_count=len(chunks),
            )

            logger.info(
                "✓ [%s] %s → %d chunks, %d points (%s)",
                collection, rel_path, len(chunks), len(points), reason,
            )

        except Exception as exc:
            result.status = "failed"
            result.error  = f"{type(exc).__name__}: {exc}"
            logger.error("✗ [%s] %s — %s", collection, rel_path, exc, exc_info=True)
            if not dry_run:
                try:
                    self.tracker.mark_failed(file_path, collection, result.error)
                except Exception:
                    pass  # Ne pas masquer l'erreur principale

        finally:
            result.duration_ms = int((time.monotonic() - t0) * 1000)

        return result

    # ── Utilities ──────────────────────────────────────────────

    def scan_only(
        self,
        source_path: str,
        allowed_extensions: Optional[list[str]] = None,
        max_files: int = 200,
    ) -> None:
        """
        Afficher l'arborescence des fichiers qui seraient ingérés.
        Ne modifie rien (ni Qdrant ni le tracker).
        """
        scanner = FileScanner(
            allowed_extensions=allowed_extensions or self.config.allowed_extensions,
        )
        scanner.print_tree(source_path, max_files=max_files)

    def reset_collection_tracker(self, collection_name: str) -> int:
        """
        Réinitialiser le tracker d'une collection.
        Le prochain `ingest()` traitera tous les fichiers comme nouveaux.
        """
        return self.tracker.reset_collection(collection_name)

    def reset_all_trackers(self) -> int:
        """Réinitialiser tous les trackers (réingestion totale au prochain run)."""
        return self.tracker.reset_all()

    def status(self, collection_name: Optional[str] = None) -> dict:
        """
        Retourne le statut complet du système d'ingestion:
          - Statistiques tracker SQLite
          - Liste des collections connues
          - Info Qdrant (si disponible)
        """
        tracker_stats       = self.tracker.stats(collection_name)
        tracker_collections = self.tracker.list_collections()

        try:
            qdrant_info = (
                self.qdrant.get_collection_info(collection_name)
                if collection_name
                else self.qdrant.get_server_info()
            )
        except Exception as e:
            qdrant_info = {"error": str(e)}

        return {
            "tracker":             tracker_stats,
            "tracker_collections": tracker_collections,
            "qdrant":              qdrant_info,
        }


# ─────────────────────────────────────────────────────────────
#  REPORT PRINTERS
# ─────────────────────────────────────────────────────────────

def _print_rich_report(report: ScanIngestionReport, console) -> None:
    """Afficher le rapport avec Rich (couleurs, tableau)."""
    from rich.table import Table
    from rich import box

    scan   = report.scan_config
    is_dry = scan.dry_run

    title = f"CIVITAS INGESTION {'[DRY RUN] ' if is_dry else ''}REPORT"
    console.print(f"\n[bold cyan]{'═' * 62}[/]")
    console.print(f"[bold white]  {title}[/]")
    console.print(f"[bold cyan]{'═' * 62}[/]")
    console.print(f"  [dim]Collection :[/] [cyan]{scan.collection_name}[/]")
    console.print(f"  [dim]Source     :[/] {scan.source_path}")
    console.print(f"  [dim]Duration   :[/] {report.duration_seconds:.1f}s")
    console.print(f"  [dim]Discovered :[/] {report.total_discovered} files\n")

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Metric",  style="dim")
    table.add_column("Count",   justify="right")

    table.add_row("✓  New / Indexed",        f"[green]{report.total_new}[/]")
    table.add_row("↻  Modified / Re-indexed", f"[yellow]{report.total_modified}[/]")
    table.add_row("⏭  Skipped (unchanged)",   f"[dim]{report.total_skipped}[/]")
    table.add_row(
        "✗  Failed",
        f"[red]{report.total_failed}[/]" if report.total_failed else "[dim]0[/]",
    )
    table.add_row("⬡  Chunks produced",      f"[cyan]{report.total_chunks}[/]")
    if not is_dry:
        table.add_row("⬡  Points in Qdrant", f"[cyan]{report.total_points}[/]")
    table.add_row("✔  Success rate",         f"{report.success_rate:.0%}")

    console.print(table)

    if report.errors:
        console.print(f"\n  [red]Errors ({len(report.errors)}):[/]")
        for err in report.errors[:10]:
            console.print(f"    [red]·[/] {err}")
        if len(report.errors) > 10:
            console.print(f"    [dim]... and {len(report.errors) - 10} more[/]")

    console.print(f"[bold cyan]{'═' * 62}[/]\n")


def _print_plain_report(report: ScanIngestionReport) -> None:
    """Afficher le rapport sans dépendance Rich."""
    sep = "═" * 62
    print(f"\n{sep}")
    print("  CIVITAS INGESTION REPORT")
    print(sep)
    print(f"  Collection  : {report.scan_config.collection_name}")
    print(f"  Source      : {report.scan_config.source_path}")
    print(f"  Discovered  : {report.total_discovered}")
    print(f"  New         : {report.total_new}")
    print(f"  Modified    : {report.total_modified}")
    print(f"  Skipped     : {report.total_skipped}")
    print(f"  Failed      : {report.total_failed}")
    print(f"  Chunks      : {report.total_chunks}")
    print(f"  Points      : {report.total_points}")
    print(f"  Success rate: {report.success_rate:.0%}")
    print(f"  Duration    : {report.duration_seconds:.1f}s")
    if report.errors:
        print(f"\n  Errors ({len(report.errors)}):")
        for err in report.errors[:10]:
            print(f"    · {err}")
    print(f"{sep}\n")

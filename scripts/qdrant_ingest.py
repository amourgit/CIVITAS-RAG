#!/usr/bin/env python3
"""
scripts/qdrant_ingest.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLI d'ingestion Qdrant pour CIVITAS.

Système d'ingestion paramétrable, ciblable, incrémental et récursif.
Compatible avec n'importe quelle organisation de fichiers (N sous-dossiers).

══ COMMANDES PRINCIPALES ══════════════════════════════════════

  # Ingestion ciblée (un répertoire précis → une collection)
  python scripts/qdrant_ingest.py ingest \\
    --path data/documents/ansible \\
    --collection ansible_docs

  # Ingestion via config YAML (scan nommé)
  python scripts/qdrant_ingest.py ingest \\
    --config config/qdrant_ingestion.yaml \\
    --scan ansible_scan

  # Ingestion de TOUS les scans définis dans la config
  python scripts/qdrant_ingest.py ingest \\
    --config config/qdrant_ingestion.yaml \\
    --all

  # Dry-run (affiche ce qui serait ingéré sans modifier Qdrant)
  python scripts/qdrant_ingest.py ingest \\
    --path data/documents/terraform \\
    --collection terraform_docs \\
    --dry-run

  # Forcer la réingestion (ignore la déduplication)
  python scripts/qdrant_ingest.py ingest \\
    --path data/documents/cicd \\
    --collection cicd_docs \\
    --force

══ RECHERCHE ══════════════════════════════════════════════════

  python scripts/qdrant_ingest.py search \\
    --query "install postgresql database" \\
    --collection ansible_docs \\
    --top-k 5

  # Recherche dans toutes les collections
  python scripts/qdrant_ingest.py search \\
    --query "deploy docker image github actions" \\
    --all-collections

══ INSPECTION ═════════════════════════════════════════════════

  # Voir l'arborescence des fichiers qui seraient ingérés
  python scripts/qdrant_ingest.py tree --path data/documents/ansible

  # Statut de toutes les collections
  python scripts/qdrant_ingest.py status

  # Statut d'une collection spécifique
  python scripts/qdrant_ingest.py status --collection ansible_docs

══ RESET ══════════════════════════════════════════════════════

  # Réinitialiser le tracker d'une collection (prochain ingest = full)
  python scripts/qdrant_ingest.py reset --collection ansible_docs

  # Réinitialiser tous les trackers
  python scripts/qdrant_ingest.py reset --all

  # Supprimer complètement une collection Qdrant
  python scripts/qdrant_ingest.py drop-collection --collection ansible_docs

══ VARIABLES D'ENVIRONNEMENT ══════════════════════════════════

  QDRANT_HOST=localhost         # Hôte Qdrant
  QDRANT_PORT=6333             # Port Qdrant
  QDRANT_API_KEY=              # API key (optionnel)
  QDRANT_URL=                  # URL Qdrant Cloud (override host/port)
  CIVITAS_TRACKER_DB=.civitas_ingestion_tracker.db
  OPENAI_API_KEY=              # Si embedding provider = openai
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ajouter le répertoire racine au path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _setup_logging(level: str = "INFO", log_file: str = None) -> None:
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    # Réduire le bruit des librairies tierces
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _load_config(config_path: str) -> "QdrantIngestionConfig":
    """Charger la config depuis YAML."""
    from civitas.ingestion.qdrant import QdrantIngestionConfig
    return QdrantIngestionConfig.from_yaml(config_path)


def _default_config() -> "QdrantIngestionConfig":
    """Config par défaut (sans YAML)."""
    from civitas.ingestion.qdrant import QdrantIngestionConfig
    return QdrantIngestionConfig()


def _load_scans_from_yaml(config_path: str) -> dict:
    """Charger les scans prédéfinis depuis le YAML."""
    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("scans", {})


# ─────────────────────────────────────────────────────────────
#  COMMANDES
# ─────────────────────────────────────────────────────────────

def cmd_ingest(args) -> int:
    """Commande: ingestion de documents."""
    from dotenv import load_dotenv
    load_dotenv()

    from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig

    # Charger la config
    if args.config:
        config = _load_config(args.config)
    else:
        config = _default_config()

    # Appliquer les overrides CLI
    if args.qdrant_host:
        config.qdrant_host = args.qdrant_host
    if args.qdrant_port:
        config.qdrant_port = args.qdrant_port

    pipeline = QdrantIngestionPipeline(config)

    # Déterminer les scans à lancer
    scans_to_run: list[ScanConfig] = []

    if args.all and args.config:
        # Tous les scans définis dans le YAML
        scans_dict = _load_scans_from_yaml(args.config)
        if not scans_dict:
            print("[ERROR] No scans defined in config file.")
            return 1
        for name, scan_data in scans_dict.items():
            sc = ScanConfig.from_dict(scan_data)
            if args.dry_run:
                sc.dry_run = True
            if args.force:
                sc.skip_existing = False
            scans_to_run.append(sc)
        print(f"[INFO] Running {len(scans_to_run)} scans from config...")

    elif args.scan and args.config:
        # Un scan nommé depuis le YAML
        scans_dict = _load_scans_from_yaml(args.config)
        if args.scan not in scans_dict:
            available = ", ".join(scans_dict.keys())
            print(f"[ERROR] Scan '{args.scan}' not found. Available: {available}")
            return 1
        sc = ScanConfig.from_dict(scans_dict[args.scan])
        if args.dry_run:
            sc.dry_run = True
        if args.force:
            sc.skip_existing = False
        scans_to_run.append(sc)

    elif args.path and args.collection:
        # Ingestion directe via CLI args
        sc = ScanConfig(
            source_path=args.path,
            collection_name=args.collection,
            recursive=not args.no_recursive,
            dry_run=args.dry_run,
            skip_existing=not args.force,
            domain=args.domain or "",
            tags=args.tags or [],
            chunk_size=args.chunk_size or config.default_chunk_size,
            chunk_overlap=args.chunk_overlap or config.default_chunk_overlap,
        )
        if args.extensions:
            sc.allowed_extensions = [
                ext if ext.startswith(".") else f".{ext}"
                for ext in args.extensions
            ]
        scans_to_run.append(sc)

    else:
        print("[ERROR] Specify --path + --collection, or --scan + --config, or --all + --config")
        return 1

    # Lancer les scans
    reports = pipeline.ingest_many(scans_to_run)

    # Afficher les rapports
    for report in reports:
        report.print_report()

    # Code de sortie
    total_failed = sum(r.total_failed for r in reports)
    return 0 if total_failed == 0 else 1


def cmd_search(args) -> int:
    """Commande: recherche sémantique."""
    from dotenv import load_dotenv
    load_dotenv()

    from civitas.ingestion.qdrant import (
        QdrantIngestionPipeline, DocumentEmbedder, CivitasQdrantClient
    )

    config = _load_config(args.config) if args.config else _default_config()
    client = CivitasQdrantClient.from_config(config)
    embedder = DocumentEmbedder.get_or_create(config.embedding)

    print(f"\n🔍 Searching: \"{args.query}\"")
    print(f"   Top-K: {args.top_k}")

    # Vectoriser la requête
    query_vector = embedder.embed_query(args.query)

    # Déterminer les collections à interroger
    if args.all_collections:
        collections = client.list_collections()
        print(f"   Collections: {collections}")
    else:
        collections = [args.collection]

    # Rechercher
    if len(collections) == 1:
        results = client.search(
            collection_name=collections[0],
            query_vector=query_vector,
            top_k=args.top_k,
            score_threshold=args.min_score,
        )
    else:
        results = client.search_across_collections(
            query_vector=query_vector,
            collection_names=collections,
            top_k=args.top_k,
            score_threshold=args.min_score,
        )

    if not results:
        print("\n  No results found.")
        return 0

    print(f"\n{'─' * 70}")
    print(f"  Found {len(results)} result(s):")
    print(f"{'─' * 70}")

    for i, r in enumerate(results, 1):
        score_bar = "█" * int(r.score * 20)
        print(f"\n  [{i}] score={r.score:.4f} {score_bar}")
        print(f"      file:    {r.relative_path}")
        print(f"      coll:    {r.collection}")
        print(f"      chunk:   #{r.chunk_index}")
        if args.show_text:
            preview = r.chunk_text[:300].replace("\n", " ")
            print(f"      text:    {preview}...")

    print(f"\n{'─' * 70}\n")
    return 0


def cmd_tree(args) -> int:
    """Commande: afficher l'arborescence d'un répertoire."""
    from civitas.ingestion.qdrant import FileScanner, QdrantIngestionConfig

    config = _load_config(args.config) if args.config else _default_config()
    scanner = FileScanner(
        allowed_extensions=config.allowed_extensions,
        max_file_size_mb=100,
    )
    result = scanner.print_tree(args.path, max_files=args.max_files)
    return 0


def cmd_status(args) -> int:
    """Commande: afficher le statut du système d'ingestion."""
    from dotenv import load_dotenv
    load_dotenv()

    from civitas.ingestion.qdrant import QdrantIngestionPipeline

    config = _load_config(args.config) if args.config else _default_config()
    pipeline = QdrantIngestionPipeline(config)

    status = pipeline.status(args.collection)

    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        console = Console()

        console.print("\n[bold cyan]═══ CIVITAS Qdrant Status ═══[/]\n")

        # Tracker stats
        tracker = status["tracker"]
        console.print("[bold]📊 Tracker (SQLite)[/]")
        console.print(f"   Total files  : [cyan]{tracker['total_files']}[/]")
        console.print(f"   Succeeded    : [green]{tracker['succeeded']}[/]")
        console.print(f"   Failed       : [red]{tracker['failed']}[/]")
        console.print(f"   Total chunks : [cyan]{tracker['total_chunks']}[/]")
        if tracker['last_ingestion']:
            console.print(f"   Last ingest  : {tracker['last_ingestion'][:19]}")

        # Collections tracker
        if status["tracker_collections"]:
            console.print("\n[bold]📁 Collections (tracker)[/]")
            t = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
            t.add_column("Collection")
            t.add_column("Files", justify="right")
            t.add_column("Chunks", justify="right")
            t.add_column("Size", justify="right")
            t.add_column("Last Ingestion")
            for col in status["tracker_collections"]:
                size_mb = (col["bytes"] or 0) / 1024 / 1024
                t.add_row(
                    col["collection"],
                    str(col["files"]),
                    str(col["chunks"]),
                    f"{size_mb:.1f}MB",
                    (col["last_ingestion"] or "")[:19],
                )
            console.print(t)

        # Qdrant status
        qdrant = status["qdrant"]
        console.print("\n[bold]⬡ Qdrant[/]")
        if "error" in qdrant:
            console.print(f"   [red]Error: {qdrant['error']}[/]")
        elif "collections" in qdrant:
            console.print(f"   Collections: {qdrant['collections']}")
        elif "points_count" in qdrant:
            console.print(f"   Points     : [cyan]{qdrant['points_count']}[/]")
            console.print(f"   Status     : {qdrant.get('status')}")

        console.print()

    except ImportError:
        print("\n=== CIVITAS Qdrant Status ===")
        print(f"Tracker: {status['tracker']}")
        print(f"Collections: {[c['collection'] for c in status['tracker_collections']]}")
        print(f"Qdrant: {status['qdrant']}")

    return 0


def cmd_reset(args) -> int:
    """Commande: réinitialiser le tracker."""
    from civitas.ingestion.qdrant import QdrantIngestionPipeline

    config = _load_config(args.config) if args.config else _default_config()
    pipeline = QdrantIngestionPipeline(config)

    if args.all:
        count = pipeline.reset_all_trackers()
        print(f"✓ All trackers reset ({count} records deleted). Next ingest will reprocess ALL files.")
    elif args.collection:
        count = pipeline.reset_collection_tracker(args.collection)
        print(f"✓ Tracker reset for '{args.collection}' ({count} records deleted).")
    else:
        print("[ERROR] Specify --collection <name> or --all")
        return 1

    return 0


def cmd_drop_collection(args) -> int:
    """Commande: supprimer une collection Qdrant."""
    from dotenv import load_dotenv
    load_dotenv()

    from civitas.ingestion.qdrant import CivitasQdrantClient

    config = _load_config(args.config) if args.config else _default_config()
    client = CivitasQdrantClient.from_config(config)

    name = args.collection
    if not client.collection_exists(name):
        print(f"[WARN] Collection '{name}' does not exist in Qdrant.")
        return 0

    confirm = input(f"⚠️  Delete collection '{name}' from Qdrant? [yes/N]: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return 0

    client.delete_collection(name)
    print(f"✓ Collection '{name}' deleted from Qdrant.")
    return 0


def cmd_collections(args) -> int:
    """Commande: lister les collections Qdrant."""
    from dotenv import load_dotenv
    load_dotenv()

    from civitas.ingestion.qdrant import CivitasQdrantClient

    config = _load_config(args.config) if args.config else _default_config()
    client = CivitasQdrantClient.from_config(config)

    if not client.health_check():
        print(f"[ERROR] Cannot connect to Qdrant at {config.qdrant_host}:{config.qdrant_port}")
        return 1

    collections = client.list_collections()
    print(f"\nQdrant collections ({len(collections)}):")
    for name in collections:
        info = client.get_collection_info(name)
        points = info.get("points_count", "?")
        status = info.get("status", "?")
        print(f"  · {name:30s}  {points:>8} points  [{status}]")
    print()
    return 0


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="qdrant_ingest",
        description="CIVITAS — Système d'ingestion Qdrant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Options globales
    parser.add_argument("--config", "-c", help="Chemin vers le fichier YAML de configuration")
    parser.add_argument("--qdrant-host", help="Hôte Qdrant (override config)")
    parser.add_argument("--qdrant-port", type=int, help="Port Qdrant (override config)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", help="Fichier de log")

    subparsers = parser.add_subparsers(dest="command", help="Commande")

    # ── ingest ────────────────────────────────────────────────
    p_ingest = subparsers.add_parser("ingest", help="Ingérer des documents")
    p_ingest.add_argument("--path", help="Répertoire ou fichier à ingérer")
    p_ingest.add_argument("--collection", help="Collection Qdrant cible")
    p_ingest.add_argument("--scan", help="Nom du scan prédéfini dans le YAML")
    p_ingest.add_argument("--all", action="store_true",
                           help="Lancer tous les scans définis dans le YAML")
    p_ingest.add_argument("--dry-run", action="store_true",
                           help="Parser sans indexer dans Qdrant")
    p_ingest.add_argument("--force", action="store_true",
                           help="Forcer la réingestion (ignorer la déduplication)")
    p_ingest.add_argument("--no-recursive", action="store_true",
                           help="Ne pas scanner les sous-dossiers")
    p_ingest.add_argument("--domain", help="Domaine métier (ex: devops, security)")
    p_ingest.add_argument("--tags", nargs="*", help="Tags à associer aux documents")
    p_ingest.add_argument("--extensions", nargs="*",
                           help="Extensions autorisées (ex: .yml .yaml .tf)")
    p_ingest.add_argument("--chunk-size", type=int, help="Taille des chunks en mots")
    p_ingest.add_argument("--chunk-overlap", type=int, help="Chevauchement en mots")

    # ── search ────────────────────────────────────────────────
    p_search = subparsers.add_parser("search", help="Recherche sémantique")
    p_search.add_argument("--query", "-q", required=True, help="Requête de recherche")
    p_search.add_argument("--collection", help="Collection à interroger")
    p_search.add_argument("--all-collections", action="store_true",
                           help="Rechercher dans toutes les collections")
    p_search.add_argument("--top-k", type=int, default=5, help="Nombre de résultats")
    p_search.add_argument("--min-score", type=float, default=0.0, help="Score minimum")
    p_search.add_argument("--show-text", action="store_true",
                           help="Afficher un extrait du texte du chunk")

    # ── tree ──────────────────────────────────────────────────
    p_tree = subparsers.add_parser("tree", help="Afficher l'arborescence d'un répertoire")
    p_tree.add_argument("--path", required=True, help="Répertoire à scanner")
    p_tree.add_argument("--max-files", type=int, default=200,
                         help="Nombre max de fichiers à afficher")

    # ── status ────────────────────────────────────────────────
    p_status = subparsers.add_parser("status", help="Statut du système d'ingestion")
    p_status.add_argument("--collection", help="Collection spécifique")

    # ── reset ─────────────────────────────────────────────────
    p_reset = subparsers.add_parser("reset", help="Réinitialiser le tracker")
    p_reset.add_argument("--collection", help="Collection à réinitialiser")
    p_reset.add_argument("--all", action="store_true",
                          help="Réinitialiser tous les trackers")

    # ── drop-collection ───────────────────────────────────────
    p_drop = subparsers.add_parser("drop-collection",
                                    help="Supprimer une collection Qdrant")
    p_drop.add_argument("--collection", required=True, help="Nom de la collection")

    # ── collections ───────────────────────────────────────────
    subparsers.add_parser("collections", help="Lister les collections Qdrant")

    args = parser.parse_args()

    # Logging
    _setup_logging(args.log_level, getattr(args, "log_file", None))

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    handlers = {
        "ingest": cmd_ingest,
        "search": cmd_search,
        "tree": cmd_tree,
        "status": cmd_status,
        "reset": cmd_reset,
        "drop-collection": cmd_drop_collection,
        "collections": cmd_collections,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    sys.exit(handler(args))


if __name__ == "__main__":
    main()

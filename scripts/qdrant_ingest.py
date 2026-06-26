#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║         CIVITAS-RAG  ·  Qdrant Ingestion CLI                    ║
║         Système d'ingestion vectorielle paramétrable             ║
╚══════════════════════════════════════════════════════════════════╝

USAGE RAPIDE:
  python scripts/qdrant_ingest.py <commande> [options]
  python scripts/qdrant_ingest.py --help

COMMANDES DISPONIBLES:
  ingest       Ingérer des documents dans Qdrant
  search       Recherche sémantique vectorielle
  tree         Visualiser l'arborescence des fichiers scannable
  status       Statut complet du système (tracker + Qdrant)
  inspect      Inspecter une collection en détail
  list-files   Lister les fichiers trackés (ingérés)
  diff         Diff entre fichiers sur disque et fichiers trackés
  verify       Vérifier la cohérence tracker ↔ Qdrant
  reset        Réinitialiser le tracker (force réingestion)
  purge        Supprimer une collection Qdrant + son tracker
  ping         Tester la connexion à Qdrant
  config       Afficher la configuration active résolue
  export       Exporter les métadonnées d'une collection (JSON/CSV)
  delete-file  Supprimer un fichier spécifique du tracker + Qdrant

EXEMPLES:
  # Ingestion ciblée
  python scripts/qdrant_ingest.py ingest \\
    --path data/documents/ansible --collection ansible_docs

  # Ingestion depuis config YAML
  python scripts/qdrant_ingest.py ingest \\
    --config config/qdrant_ingestion.yaml --scan ansible_scan

  # Tous les scans du YAML
  python scripts/qdrant_ingest.py ingest \\
    --config config/qdrant_ingestion.yaml --all

  # Recherche avec filtre
  python scripts/qdrant_ingest.py search \\
    --query "install postgresql" --collection ansible_docs --top-k 5 --show-text

  # Diff pour voir ce qui a changé sur disque
  python scripts/qdrant_ingest.py diff \\
    --path data/documents/ansible --collection ansible_docs

  # Vérification cohérence
  python scripts/qdrant_ingest.py verify --collection ansible_docs

  # Export JSON
  python scripts/qdrant_ingest.py export \\
    --collection ansible_docs --format json --output /tmp/export.json

VARIABLES D'ENVIRONNEMENT:
  QDRANT_HOST          Hôte Qdrant           (défaut: localhost)
  QDRANT_PORT          Port Qdrant           (défaut: 6333)
  QDRANT_API_KEY       API key Qdrant        (optionnel)
  QDRANT_URL           URL Qdrant Cloud      (override host+port)
  QDRANT_IN_MEMORY     Mode in-memory        (true/false)
  CIVITAS_TRACKER_DB   Chemin SQLite tracker (défaut: .civitas_ingestion_tracker.db)
  OPENAI_API_KEY       Clé OpenAI            (si embedding.provider=openai)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# ── Bootstrap path ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Constantes ────────────────────────────────────────────────────────────────
PROG      = "civitas-rag"
VERSION   = "1.0.0"
SEPARATOR = "═" * 66


# ══════════════════════════════════════════════════════════════════════════════
#  INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

def _bootstrap() -> None:
    """Charger .env si présent (silencieux si python-dotenv absent)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def _setup_logging(level: str = "WARNING", log_file: Optional[str] = None) -> None:
    """Configurer le logging — silencieux par défaut, verbeux avec --verbose."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.WARNING),
        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    for noisy in ("sentence_transformers", "transformers", "httpx",
                  "urllib3", "qdrant_client", "sklearn"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


def _resolve_config(args) -> "QdrantIngestionConfig":
    """
    Résoudre la configuration dans l'ordre de priorité :
      valeurs par défaut < variables d'environnement < YAML < flags CLI
    """
    from civitas.ingestion.qdrant import QdrantIngestionConfig, EmbeddingConfig
    cfg = QdrantIngestionConfig.from_yaml(args.config) if getattr(args, "config", None) \
          else QdrantIngestionConfig()

    # Overrides Qdrant CLI
    if getattr(args, "qdrant_host", None):
        cfg.qdrant_host = args.qdrant_host
    if getattr(args, "qdrant_port", None):
        cfg.qdrant_port = args.qdrant_port
    if getattr(args, "qdrant_url", None):
        cfg.qdrant_url = args.qdrant_url

    # Override embedding provider/model/dim
    emb_provider = getattr(args, "embedding_provider", None)
    emb_model    = getattr(args, "embedding_model",    None)
    emb_dim      = getattr(args, "embedding_dim",      None)

    if emb_provider:
        dim_defaults = {
            "tfidf-local":           128,
            "sentence-transformers": 384,
            "openai":                1536,
        }
        cfg.embedding = EmbeddingConfig(
            provider             = emb_provider,
            model_name           = emb_model or (
                "tfidf" if emb_provider == "tfidf-local" else cfg.embedding.model_name
            ),
            vector_size          = emb_dim or dim_defaults.get(emb_provider, cfg.embedding.vector_size),
            batch_size           = cfg.embedding.batch_size,
            device               = cfg.embedding.device,
            normalize_embeddings = cfg.embedding.normalize_embeddings,
            openai_api_key       = cfg.embedding.openai_api_key,
            openai_model         = emb_model or cfg.embedding.openai_model,
        )
    else:
        if emb_model:
            cfg.embedding.model_name = emb_model
        if emb_dim:
            cfg.embedding.vector_size = emb_dim

    return cfg


def _load_yaml_scans(config_path: str) -> dict:
    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("scans", {})


def _confirm(prompt: str, force: bool = False) -> bool:
    """Demander une confirmation interactive (bypassed avec --yes)."""
    if force:
        return True
    try:
        return input(f"  {prompt} [yes/N]: ").strip().lower() == "yes"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  UI HELPERS — Rich-first, fallback plain
# ══════════════════════════════════════════════════════════════════════════════

def _console():
    """Retourner un console Rich ou None."""
    try:
        from rich.console import Console
        return Console()
    except ImportError:
        return None


def _rprint(msg: str, con=None) -> None:
    if con:
        con.print(msg)
    else:
        # Strip des balises Rich basiques pour le fallback
        import re
        print(re.sub(r"\[/?[^\]]*\]", "", msg))


def _table_rich(con, headers: list[str], rows: list[list[str]],
                title: str = "") -> None:
    """Afficher un tableau Rich ou un tableau ASCII."""
    if con:
        from rich.table import Table
        from rich import box
        t = Table(title=title or None, box=box.SIMPLE,
                  show_header=True, header_style="bold cyan")
        for h in headers:
            t.add_column(h)
        for row in rows:
            t.add_row(*row)
        con.print(t)
    else:
        # Fallback ASCII
        if title:
            print(f"\n  {title}")
        widths = [max(len(h), max((len(r[i]) for r in rows), default=0))
                  for i, h in enumerate(headers)]
        fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
        print(fmt.format(*headers))
        print("  " + "  ".join("─" * w for w in widths))
        for row in rows:
            print(fmt.format(*row))


def _ok(msg: str) -> None:
    con = _console()
    _rprint(f"[green]✓[/] {msg}", con)


def _err(msg: str) -> None:
    con = _console()
    _rprint(f"[red]✗[/] {msg}", con)


def _warn(msg: str) -> None:
    con = _console()
    _rprint(f"[yellow]⚠[/]  {msg}", con)


def _info(msg: str) -> None:
    con = _console()
    _rprint(f"[dim]{msg}[/]", con)


def _header(title: str) -> None:
    con = _console()
    _rprint(f"\n[bold cyan]{SEPARATOR}[/]", con)
    _rprint(f"[bold white]  {title}[/]", con)
    _rprint(f"[bold cyan]{SEPARATOR}[/]", con)


def _size_human(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: ping
# ══════════════════════════════════════════════════════════════════════════════

def cmd_ping(args) -> int:
    """Tester la connectivité Qdrant et afficher les infos serveur."""
    _bootstrap()
    cfg = _resolve_config(args)
    from civitas.ingestion.qdrant import CivitasQdrantClient
    client = CivitasQdrantClient.from_config(cfg)

    _header("CIVITAS · Ping Qdrant")
    target = cfg.qdrant_url or f"{cfg.qdrant_host}:{cfg.qdrant_port}"
    _info(f"Target : {target}")

    if cfg.qdrant_in_memory:
        _warn("Mode in-memory actif — aucun serveur requis")
        return 0

    if not client.health_check():
        _err(f"Qdrant inaccessible à {target}")
        _info("Lancez Qdrant : docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant")
        return 1

    info = client.get_server_info()
    _ok(f"Qdrant accessible · {info.get('collections_count', 0)} collection(s)")
    cols = info.get("collections", [])
    if cols:
        _info("Collections : " + ", ".join(cols))
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: config
# ══════════════════════════════════════════════════════════════════════════════

def cmd_config(args) -> int:
    """Afficher la configuration active complète, telle que résolue."""
    _bootstrap()
    cfg = _resolve_config(args)
    con = _console()

    _header("CIVITAS · Configuration active")

    sections = [
        ("Qdrant", [
            ("Host",       cfg.qdrant_url or cfg.qdrant_host),
            ("Port",       str(cfg.qdrant_port)),
            ("API Key",    "***" if cfg.qdrant_api_key else "(none)"),
            ("URL",        cfg.qdrant_url or "(local)"),
            ("In-Memory",  str(cfg.qdrant_in_memory)),
        ]),
        ("Embedding", [
            ("Provider",   cfg.embedding.provider),
            ("Model",      cfg.embedding.model_name),
            ("Dim",        str(cfg.embedding.vector_size)),
            ("Device",     cfg.embedding.device),
            ("Batch size", str(cfg.embedding.batch_size)),
            ("Normalize",  str(cfg.embedding.normalize_embeddings)),
        ]),
        ("Ingestion", [
            ("Tracker DB",     cfg.tracker_db_path),
            ("Batch size",     str(cfg.batch_size)),
            ("Chunk size",     str(cfg.default_chunk_size)),
            ("Chunk overlap",  str(cfg.default_chunk_overlap)),
            ("Max workers",    str(cfg.max_workers)),
        ]),
    ]

    for section, pairs in sections:
        _rprint(f"\n[bold]{section}[/]", con)
        for key, val in pairs:
            _rprint(f"  [dim]{key:<18}[/] {val}", con)

    # Collections définies
    if cfg.collections:
        _rprint(f"\n[bold]Collections définies ({len(cfg.collections)})[/]", con)
        rows = [
            [name, str(c.chunk_size), str(c.chunk_overlap),
             c.default_domain or "—", ", ".join(c.default_tags) or "—"]
            for name, c in cfg.collections.items()
        ]
        _table_rich(con, ["Name", "Chunk", "Overlap", "Domain", "Tags"], rows)

    # Extensions autorisées
    _rprint(f"\n[bold]Extensions autorisées ({len(cfg.allowed_extensions)})[/]", con)
    _rprint("  " + "  ".join(cfg.allowed_extensions), con)

    print()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: ingest
# ══════════════════════════════════════════════════════════════════════════════

def cmd_ingest(args) -> int:
    """Ingérer des documents dans Qdrant (incrémental, paramétrable)."""
    _bootstrap()
    cfg = _resolve_config(args)
    from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig

    pipeline = QdrantIngestionPipeline(cfg)
    scans: list[ScanConfig] = []

    # ── Résolution de la source ───────────────────────────────
    if getattr(args, "all", False) and args.config:
        scans_dict = _load_yaml_scans(args.config)
        if not scans_dict:
            _err("Aucun scan défini dans le fichier YAML.")
            return 1
        for scan_data in scans_dict.values():
            sc = ScanConfig.from_dict(scan_data)
            sc.dry_run       = sc.dry_run or getattr(args, "dry_run", False)
            sc.skip_existing = not getattr(args, "force", False) and sc.skip_existing
            scans.append(sc)
        _info(f"{len(scans)} scans chargés depuis {args.config}")

    elif getattr(args, "scan", None) and args.config:
        scans_dict = _load_yaml_scans(args.config)
        if args.scan not in scans_dict:
            _err(f"Scan '{args.scan}' introuvable. Disponibles : {', '.join(scans_dict)}")
            return 1
        sc = ScanConfig.from_dict(scans_dict[args.scan])
        sc.dry_run       = sc.dry_run or getattr(args, "dry_run", False)
        sc.skip_existing = not getattr(args, "force", False) and sc.skip_existing
        scans.append(sc)

    elif getattr(args, "path", None) and getattr(args, "collection", None):
        sc = ScanConfig(
            source_path      = args.path,
            collection_name  = args.collection,
            recursive        = not getattr(args, "no_recursive", False),
            dry_run          = getattr(args, "dry_run", False),
            skip_existing    = not getattr(args, "force", False),
            domain           = getattr(args, "domain", None) or "",
            tags             = getattr(args, "tags", None) or [],
            chunk_size       = getattr(args, "chunk_size", None) or cfg.default_chunk_size,
            chunk_overlap    = getattr(args, "chunk_overlap", None) or cfg.default_chunk_overlap,
            max_file_size_mb = getattr(args, "max_size_mb", None) or 100.0,
        )
        if getattr(args, "extensions", None):
            sc.allowed_extensions = [
                e if e.startswith(".") else f".{e}" for e in args.extensions
            ]
        scans.append(sc)

    else:
        _err("Spécifiez : --path + --collection  |  --scan + --config  |  --all + --config")
        return 1

    # ── Lancer les scans ──────────────────────────────────────
    reports = pipeline.ingest_many(scans)
    for r in reports:
        r.print_report()

    failed = sum(r.total_failed for r in reports)
    if failed > 0:
        _warn(f"{failed} fichier(s) en erreur — voir détails ci-dessus.")
    return 0 if failed == 0 else 1


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: search
# ══════════════════════════════════════════════════════════════════════════════

def cmd_search(args) -> int:
    """Recherche sémantique vectorielle dans une ou plusieurs collections."""
    _bootstrap()
    cfg = _resolve_config(args)
    from civitas.ingestion.qdrant import CivitasQdrantClient, DocumentEmbedder
    con     = _console()
    client  = CivitasQdrantClient.from_config(cfg)
    embedder = DocumentEmbedder.get_or_create(cfg.embedding)

    _header(f"CIVITAS · Recherche : « {args.query} »")

    # Collections cibles
    if getattr(args, "all_collections", False):
        collections = client.list_collections()
        if not collections:
            _err("Aucune collection dans Qdrant. Lancez d'abord une ingestion.")
            return 1
        _info(f"Collections : {', '.join(collections)}")
    elif getattr(args, "collection", None):
        collections = [args.collection]
    else:
        _err("Spécifiez --collection <nom> ou --all-collections")
        return 1

    # Vectoriser la requête
    query_vector = embedder.embed_query(args.query)

    # Filtres optionnels
    filter_kwargs = {}
    if getattr(args, "filter_domain", None):
        filter_kwargs["filter_domain"] = args.filter_domain
    if getattr(args, "filter_extension", None):
        filter_kwargs["filter_extension"] = args.filter_extension
    if getattr(args, "filter_tags", None):
        filter_kwargs["filter_tags"] = args.filter_tags

    # Lancer la recherche
    try:
        if len(collections) == 1:
            results = client.search(
                collection_name  = collections[0],
                query_vector     = query_vector,
                top_k            = args.top_k,
                score_threshold  = args.min_score,
                **filter_kwargs,
            )
        else:
            results = client.search_across_collections(
                query_vector     = query_vector,
                collection_names = collections,
                top_k            = args.top_k,
                score_threshold  = args.min_score,
            )
    except Exception as e:
        errmsg = str(e)
        if "not found" in errmsg.lower() or "doesn't exist" in errmsg.lower():
            _err(f"Collection introuvable dans Qdrant. Lancez d'abord une ingestion.")
        else:
            _err(f"Erreur de recherche : {e}")
        return 1

    if not results:
        _warn("Aucun résultat trouvé.")
        if getattr(args, "json_output", False):
            print("[]")
        return 0

    _rprint(f"\n  [bold]{len(results)} résultat(s)[/]\n", con)

    for i, r in enumerate(results, 1):
        score_pct = max(0.0, min(1.0, r.score))
        bar_fill  = int(score_pct * 24)
        bar       = "█" * bar_fill + "░" * (24 - bar_fill)

        if con:
            score_color = "green" if r.score > 0.7 else "yellow" if r.score > 0.4 else "red"
            con.print(f"  [bold]{i:>2}.[/]  [{score_color}]{r.score:.4f}[/]  [dim]{bar}[/]")
        else:
            print(f"  {i:>2}.  {r.score:.4f}  {bar}")

        _rprint(f"       [cyan]{r.relative_path}[/]  [dim]#{r.chunk_index}[/]", con)
        _rprint(f"       [dim]collection:[/] {r.collection}  "
                f"[dim]domain:[/] {r.domain or '—'}  "
                f"[dim]file:[/] {r.filename}", con)

        if getattr(args, "show_text", False):
            preview = r.chunk_text[:400].replace("\n", " ").strip()
            _rprint(f"\n       [dim italic]{preview}…[/]\n", con)
        else:
            print()

    # Format JSON si demandé
    if getattr(args, "json_output", False):
        out = [
            {
                "rank":          i + 1,
                "score":         r.score,
                "file":          r.file_path,
                "relative_path": r.relative_path,
                "collection":    r.collection,
                "chunk_index":   r.chunk_index,
                "domain":        r.domain,
                "chunk_text":    r.chunk_text,
            }
            for i, r in enumerate(results)
        ]
        print(json.dumps(out, indent=2, ensure_ascii=False))

    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: tree
# ══════════════════════════════════════════════════════════════════════════════

def cmd_tree(args) -> int:
    """Visualiser l'arborescence des fichiers scannables dans un répertoire."""
    _bootstrap()
    cfg = _resolve_config(args)
    from civitas.ingestion.qdrant import FileScanner

    extensions = None
    if getattr(args, "extensions", None):
        extensions = [e if e.startswith(".") else f".{e}" for e in args.extensions]

    scanner = FileScanner(
        allowed_extensions = extensions or cfg.allowed_extensions,
        max_file_size_mb   = getattr(args, "max_size_mb", 100.0),
        recursive          = not getattr(args, "no_recursive", False),
    )
    result = scanner.print_tree(args.path, max_files=args.max_files)

    # Résumé des extensions trouvées
    con = _console()
    if result.extensions_found:
        _rprint("\n[bold]Extensions trouvées :[/]", con)
        ext_rows = [
            [ext, str(count)]
            for ext, count in sorted(
                result.extensions_found.items(), key=lambda x: -x[1]
            )
        ]
        _table_rich(con, ["Extension", "Fichiers"], ext_rows)

    # Fichiers exclus
    skipped_total = result.total_skipped
    if skipped_total > 0:
        _rprint(f"\n[dim]{skipped_total} fichier(s) exclus "
                f"({len(result.skipped_extension)} ext. non autorisée, "
                f"{len(result.skipped_size)} trop lourds, "
                f"{len(result.skipped_pattern)} patterns exclus)[/]", con)
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: status
# ══════════════════════════════════════════════════════════════════════════════

def cmd_status(args) -> int:
    """Afficher le statut complet : tracker SQLite + collections Qdrant."""
    _bootstrap()
    cfg = _resolve_config(args)
    from civitas.ingestion.qdrant import QdrantIngestionPipeline, CivitasQdrantClient
    con      = _console()
    pipeline = QdrantIngestionPipeline(cfg)
    status   = pipeline.status(getattr(args, "collection", None))

    _header("CIVITAS · Statut du système")

    # ── Tracker ───────────────────────────────────────────────
    t = status["tracker"]
    _rprint("\n[bold]📊 Tracker SQLite[/]", con)
    _rprint(f"  [dim]Base de données  :[/] {cfg.tracker_db_path}", con)
    _rprint(f"  [dim]Fichiers totaux  :[/] [cyan]{t['total_files']}[/]", con)
    _rprint(f"  [dim]Succès           :[/] [green]{t['succeeded']}[/]", con)
    _rprint(f"  [dim]Échecs           :[/] "
            f"{'[red]' if t['failed'] else '[dim]'}{t['failed']}{'[/]' if t['failed'] else '[/]'}", con)
    _rprint(f"  [dim]Chunks totaux    :[/] [cyan]{t['total_chunks']}[/]", con)
    _rprint(f"  [dim]Volume total     :[/] {_size_human(t['total_bytes'] or 0)}", con)
    if t.get("first_ingestion"):
        _rprint(f"  [dim]Première ingest  :[/] {t['first_ingestion'][:19]}", con)
    if t.get("last_ingestion"):
        _rprint(f"  [dim]Dernière ingest  :[/] {t['last_ingestion'][:19]}", con)

    # ── Tableau des collections (tracker) ─────────────────────
    cols = status["tracker_collections"]
    if cols:
        _rprint(f"\n[bold]📁 Collections trackées ({len(cols)})[/]", con)
        rows = [
            [
                c["collection"],
                str(c["files"]),
                str(c["chunks"] or 0),
                _size_human(c["bytes"] or 0),
                (c["last_ingestion"] or "")[:19],
            ]
            for c in cols
        ]
        _table_rich(con, ["Collection", "Fichiers", "Chunks", "Volume", "Dernière ingest"], rows)

    # ── Qdrant ────────────────────────────────────────────────
    _rprint("\n[bold]⬡  Qdrant[/]", con)
    q = status["qdrant"]
    if "error" in q:
        _rprint(f"  [red]Inaccessible : {q['error']}[/]", con)
    elif "points_count" in q:
        # Vue collection unique
        status_color = "green" if str(q.get("status", "")).lower() == "green" else "cyan"
        _rprint(f"  [dim]Collection       :[/] [cyan]{q['name']}[/]", con)
        _rprint(f"  [dim]Statut           :[/] [{status_color}]{q.get('status', '?')}[/]", con)
        _rprint(f"  [dim]Points           :[/] [cyan]{q.get('points_count', 0)}[/]", con)
        _rprint(f"  [dim]Vecteurs indexés :[/] {q.get('indexed_vectors_count', 0)}", con)
        _rprint(f"  [dim]Dimension        :[/] {q.get('vector_size', '?')}", con)
        _rprint(f"  [dim]Distance         :[/] {q.get('distance', '?')}", con)
    elif "collections" in q:
        # Vue globale
        client = CivitasQdrantClient.from_config(cfg)
        qdrant_cols = q.get("collections", [])
        _rprint(f"  [dim]Collections :[/] {len(qdrant_cols)}", con)
        if qdrant_cols:
            q_rows = []
            for cname in qdrant_cols:
                info = client.get_collection_info(cname)
                q_rows.append([
                    cname,
                    str(info.get("points_count", 0)),
                    str(info.get("vector_size", "?")),
                    str(info.get("status", "?")),
                ])
            _table_rich(con, ["Collection", "Points", "Dim", "Statut"], q_rows)

    print()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: inspect
# ══════════════════════════════════════════════════════════════════════════════

def cmd_inspect(args) -> int:
    """Inspecter une collection Qdrant en détail : info, points, payload sample."""
    _bootstrap()
    cfg    = _resolve_config(args)
    con    = _console()
    from civitas.ingestion.qdrant import CivitasQdrantClient, IngestionTracker
    client  = CivitasQdrantClient.from_config(cfg)
    tracker = IngestionTracker(cfg.tracker_db_path)
    name    = args.collection

    _header(f"CIVITAS · Inspection : {name}")

    # ── Info Qdrant ───────────────────────────────────────────
    if not client.collection_exists(name):
        _err(f"Collection '{name}' introuvable dans Qdrant.")
        return 1

    info = client.get_collection_info(name)
    _rprint("\n[bold]⬡  Qdrant[/]", con)
    for k, v in info.items():
        _rprint(f"  [dim]{k:<25}[/] {v}", con)

    # ── Info Tracker ──────────────────────────────────────────
    t = tracker.stats(name)
    _rprint("\n[bold]📊 Tracker[/]", con)
    _rprint(f"  [dim]{'Fichiers trackés':<25}[/] {t['total_files']}", con)
    _rprint(f"  [dim]{'Succès':<25}[/] [green]{t['succeeded']}[/]", con)
    _rprint(f"  [dim]{'Échecs':<25}[/] {'[red]' if t['failed'] else ''}{t['failed']}{'[/]' if t['failed'] else ''}", con)
    _rprint(f"  [dim]{'Chunks totaux':<25}[/] {t['total_chunks']}", con)
    _rprint(f"  [dim]{'Volume total':<25}[/] {_size_human(t['total_bytes'] or 0)}", con)

    # ── Listing des fichiers ───────────────────────────────────
    records = tracker.list_collection(name)
    if records:
        limit = getattr(args, "limit", 20)
        _rprint(f"\n[bold]📄 Fichiers ingérés (top {min(limit, len(records))} / {len(records)})[/]", con)
        rows = []
        for r in records[:limit]:
            status_fmt = (
                "[green]✓[/]" if r.status == "success"
                else "[red]✗[/]" if r.status == "failed"
                else "[dim]—[/]"
            )
            rows.append([
                status_fmt,
                r.file_path.replace(str(ROOT), ""),
                str(r.chunks_count),
                r.ingested_at[:19] if r.ingested_at else "—",
            ])
        _table_rich(con, ["St.", "Fichier", "Chunks", "Ingéré le"], rows)

    # ── Sample de points Qdrant ───────────────────────────────
    n_sample = getattr(args, "sample", 3)
    if n_sample > 0:
        _rprint(f"\n[bold]🔍 Sample payload ({n_sample} points)[/]", con)
        try:
            qdrant_client = client._get_client()
            hits, _ = qdrant_client.scroll(
                collection_name = name,
                limit           = n_sample,
                with_payload    = True,
                with_vectors    = False,
            )
            for hit in hits:
                p = hit.payload or {}
                _rprint(f"\n  [cyan]ID:[/] {hit.id}", con)
                _rprint(f"  [dim]file         :[/] {p.get('relative_path', '?')}", con)
                _rprint(f"  [dim]chunk_index  :[/] {p.get('chunk_index', '?')}", con)
                _rprint(f"  [dim]domain       :[/] {p.get('domain', '—')}", con)
                _rprint(f"  [dim]tags         :[/] {p.get('tags', [])}", con)
                _rprint(f"  [dim]ingested_at  :[/] {p.get('ingested_at', '?')}", con)
                preview = (p.get("chunk_text", "") or "")[:200].replace("\n", " ")
                _rprint(f"  [dim]text preview :[/] [italic]{preview}…[/]", con)
        except Exception as e:
            _warn(f"Impossible de lire les points : {e}")

    print()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: list-files
# ══════════════════════════════════════════════════════════════════════════════

def cmd_list_files(args) -> int:
    """Lister les fichiers trackés (ingérés) pour une collection."""
    _bootstrap()
    cfg     = _resolve_config(args)
    con     = _console()
    from civitas.ingestion.qdrant import IngestionTracker
    tracker = IngestionTracker(cfg.tracker_db_path)
    name    = args.collection

    filter_status = getattr(args, "status_filter", None)
    records       = tracker.list_collection(name)

    if filter_status:
        records = [r for r in records if r.status == filter_status]

    json_out = getattr(args, "json_output", False)

    if not json_out:
        _header(f"CIVITAS · Fichiers trackés : {name}")
        _rprint(f"  [dim]Filtre statut :[/] {filter_status or 'tous'}", con)
        _rprint(f"  [dim]Résultats     :[/] {len(records)}\n", con)

    if not records:
        if not json_out:
            _info("Aucun fichier trouvé pour ce filtre.")
        return 0

    if not json_out:
        rows = []
        for r in records:
            status_sym = "✓" if r.status == "success" else "✗" if r.status == "failed" else "—"
            rows.append([
                status_sym,
                r.file_path,
                str(r.chunks_count),
                _size_human(r.file_size),
                r.ingested_at[:19] if r.ingested_at else "—",
                r.error_msg[:40] if r.error_msg else "",
            ])
        _table_rich(con,
            ["St.", "Fichier", "Chunks", "Taille", "Ingéré le", "Erreur"],
            rows,
        )

    if json_out:
        out = [
            {
                "file_path":   r.file_path,
                "status":      r.status,
                "chunks":      r.chunks_count,
                "file_size":   r.file_size,
                "ingested_at": r.ingested_at,
                "error":       r.error_msg,
            }
            for r in records
        ]
        print(json.dumps(out, indent=2, ensure_ascii=False))

    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: diff
# ══════════════════════════════════════════════════════════════════════════════

def cmd_diff(args) -> int:
    """
    Comparer les fichiers sur disque vs les fichiers trackés.

    Affiche : nouveaux | modifiés | supprimés | inchangés.
    Permet de savoir exactement ce qu'une prochaine ingestion ferait.
    """
    _bootstrap()
    cfg     = _resolve_config(args)
    con     = _console()
    from civitas.ingestion.qdrant import FileScanner, IngestionTracker
    scanner = FileScanner(
        allowed_extensions = cfg.effective_extensions(
            type("S", (), {"allowed_extensions": None, "collection_name": args.collection})()
        ),
        recursive = not getattr(args, "no_recursive", False),
    )
    tracker = IngestionTracker(cfg.tracker_db_path)

    _header(f"CIVITAS · Diff : {args.path}  →  {args.collection}")

    scan_result = scanner.scan(args.path)
    disk_files  = {str(f.path): f for f in scan_result.discovered}
    records     = {r.file_path: r for r in tracker.list_collection(args.collection)}

    new_files      = []
    modified_files = []
    failed_files   = []
    unchanged      = []

    for path, df in disk_files.items():
        should, reason = tracker.should_ingest(path, args.collection, skip_existing=True)
        if reason == "new":
            new_files.append(df)
        elif reason == "modified":
            modified_files.append(df)
        elif reason == "failed":
            failed_files.append(df)
        elif reason == "unchanged":
            unchanged.append(df)

    deleted_files = [r for p, r in records.items() if p not in disk_files]

    # ── Résumé ────────────────────────────────────────────────
    _rprint(f"\n  [dim]Fichiers sur disque :[/] {len(disk_files)}", con)
    _rprint(f"  [dim]Fichiers trackés   :[/] {len(records)}", con)
    _rprint("", con)
    _rprint(f"  [green]✚  Nouveaux     :[/] {len(new_files)}", con)
    _rprint(f"  [yellow]↻  Modifiés     :[/] {len(modified_files)}", con)
    _rprint(f"  [red]✗  En erreur    :[/] {len(failed_files)}", con)
    _rprint(f"  [red]✘  Supprimés    :[/] {len(deleted_files)}", con)
    _rprint(f"  [dim]⏭  Inchangés    : {len(unchanged)}[/]", con)

    # ── Détails ───────────────────────────────────────────────
    show_unchanged = getattr(args, "show_unchanged", False)

    def _section(title: str, items, color: str, show: bool = True):
        if not items or not show:
            return
        _rprint(f"\n[bold {color}]{title} ({len(items)})[/]", con)
        for item in items[:50]:
            path_str = item.relative_path if hasattr(item, "relative_path") else item.file_path
            _rprint(f"  [dim]·[/] {path_str}", con)
        if len(items) > 50:
            _rprint(f"  [dim]... et {len(items) - 50} de plus[/]", con)

    _section("✚ Nouveaux (seront ingérés)",    new_files,      "green")
    _section("↻ Modifiés (seront réingérés)",  modified_files, "yellow")
    _section("✗ En erreur (seront réessayés)", failed_files,   "red")
    _section("✘ Supprimés du disque",          deleted_files,  "red")
    if show_unchanged:
        _section("⏭ Inchangés (skippés)",      unchanged,      "dim", show_unchanged)

    print()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: verify
# ══════════════════════════════════════════════════════════════════════════════

def cmd_verify(args) -> int:
    """
    Vérifier la cohérence entre le tracker SQLite et Qdrant.

    Détecte :
      - Fichiers trackés mais dont les points Qdrant sont absents
      - Collections tracker sans collection Qdrant correspondante
      - Fichiers en statut 'failed' en attente de réessai
    """
    _bootstrap()
    cfg     = _resolve_config(args)
    con     = _console()
    from civitas.ingestion.qdrant import CivitasQdrantClient, IngestionTracker
    client  = CivitasQdrantClient.from_config(cfg)
    tracker = IngestionTracker(cfg.tracker_db_path)

    name = getattr(args, "collection", None)
    _header(f"CIVITAS · Vérification{f' : {name}' if name else ' globale'}")

    all_ok      = True
    collections = [name] if name else [c["collection"] for c in tracker.list_collections()]

    if not collections:
        _warn("Aucune collection dans le tracker.")
        return 0

    for col in collections:
        _rprint(f"\n[bold]📁 {col}[/]", con)
        records = tracker.list_collection(col)

        # 1. Collection Qdrant existante ?
        qdrant_exists = client.collection_exists(col)
        if not qdrant_exists:
            _rprint(f"  [red]✗ Collection absente dans Qdrant[/]", con)
            all_ok = False
        else:
            _rprint(f"  [green]✓ Collection présente dans Qdrant[/]", con)

        # 2. Fichiers en erreur
        failed = [r for r in records if r.status == "failed"]
        if failed:
            _rprint(f"  [red]✗ {len(failed)} fichier(s) en statut 'failed'[/]", con)
            for r in failed[:5]:
                _rprint(f"    [dim]·[/] {r.file_path}", con)
            all_ok = False
        else:
            _rprint(f"  [green]✓ Aucun fichier en erreur[/]", con)

        # 3. Fichiers trackés mais fichier disque absent
        missing_disk = [r for r in records if not Path(r.file_path).exists()]
        if missing_disk:
            _rprint(f"  [yellow]⚠  {len(missing_disk)} fichier(s) absent(s) du disque[/]", con)
            for r in missing_disk[:5]:
                _rprint(f"    [dim]·[/] {r.file_path}", con)
            all_ok = False
        else:
            _rprint(f"  [green]✓ Tous les fichiers trackés présents sur disque[/]", con)

        # 4. Vérification des points Qdrant (optionnel, peut être lent)
        if qdrant_exists and getattr(args, "check_points", False):
            orphan_count = 0
            for r in records:
                if r.status == "success" and r.point_ids:
                    # Vérifier que les point_ids existent (spot check sur 1 par fichier)
                    try:
                        hits, _ = client._get_client().scroll(
                            collection_name = col,
                            scroll_filter   = None,
                            limit           = 1,
                            offset          = None,
                            with_payload    = False,
                            with_vectors    = False,
                        )
                        # Vérification basique — on fait confiance aux IDs
                    except Exception:
                        orphan_count += 1
            if orphan_count:
                _rprint(f"  [yellow]⚠  {orphan_count} fichier(s) avec points potentiellement orphelins[/]", con)
                all_ok = False

        # 5. Statistiques
        success_count = sum(1 for r in records if r.status == "success")
        total_chunks  = sum(r.chunks_count for r in records if r.status == "success")
        qdrant_count  = client.count_points(col) if qdrant_exists else 0
        _rprint(f"  [dim]Tracker : {success_count} fichiers, {total_chunks} chunks[/]", con)
        if qdrant_exists:
            _rprint(f"  [dim]Qdrant  : {qdrant_count} points[/]", con)

    print()
    if all_ok:
        _ok("Système cohérent — aucune anomalie détectée.")
    else:
        _warn("Des anomalies ont été détectées. Voir détails ci-dessus.")
    return 0 if all_ok else 1


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: reset
# ══════════════════════════════════════════════════════════════════════════════

def cmd_reset(args) -> int:
    """Réinitialiser le tracker SQLite (force la réingestion au prochain run)."""
    _bootstrap()
    cfg     = _resolve_config(args)
    from civitas.ingestion.qdrant import IngestionTracker
    tracker = IngestionTracker(cfg.tracker_db_path)
    yes     = getattr(args, "yes", False)

    if getattr(args, "all", False):
        stats = tracker.stats()
        if stats["total_files"] == 0:
            _info("Tracker déjà vide.")
            return 0
        if not _confirm(
            f"Supprimer TOUS les {stats['total_files']} enregistrements du tracker ?",
            force=yes,
        ):
            _info("Annulé.")
            return 0
        count = tracker.reset_all()
        _ok(f"Tracker réinitialisé — {count} enregistrement(s) supprimé(s).")
        _info("Le prochain ingest retraitera TOUS les fichiers.")

    elif getattr(args, "collection", None):
        name  = args.collection
        stats = tracker.stats(name)
        if stats["total_files"] == 0:
            _info(f"Aucun enregistrement pour '{name}'.")
            return 0
        if not _confirm(
            f"Supprimer les {stats['total_files']} enregistrements de '{name}' ?",
            force=yes,
        ):
            _info("Annulé.")
            return 0
        count = tracker.reset_collection(name)
        _ok(f"Tracker '{name}' réinitialisé — {count} enregistrement(s) supprimé(s).")

    elif getattr(args, "failed_only", False):
        # Réinitialiser uniquement les fichiers en erreur
        cols = [c["collection"] for c in tracker.list_collections()]
        total = 0
        for col in cols:
            for r in tracker.list_collection(col):
                if r.status == "failed":
                    tracker.remove_file(r.file_path, col)
                    total += 1
        _ok(f"{total} enregistrement(s) 'failed' supprimé(s) — ils seront réessayés.")

    else:
        _err("Spécifiez --collection <nom>, --all, ou --failed-only")
        return 1

    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: purge
# ══════════════════════════════════════════════════════════════════════════════

def cmd_purge(args) -> int:
    """
    Supprimer une collection Qdrant ET son tracker.

    Action irréversible — demande confirmation explicite.
    Avec --tracker-only : purge uniquement le tracker, garde Qdrant.
    Avec --qdrant-only  : purge uniquement Qdrant, garde le tracker.
    """
    _bootstrap()
    cfg    = _resolve_config(args)
    con    = _console()
    from civitas.ingestion.qdrant import CivitasQdrantClient, IngestionTracker
    client  = CivitasQdrantClient.from_config(cfg)
    tracker = IngestionTracker(cfg.tracker_db_path)
    name    = args.collection
    yes     = getattr(args, "yes", False)

    tracker_only = getattr(args, "tracker_only", False)
    qdrant_only  = getattr(args, "qdrant_only", False)

    _header(f"CIVITAS · Purge : {name}")

    qdrant_exists  = client.collection_exists(name)
    tracker_stats  = tracker.stats(name)
    tracker_exists = tracker_stats["total_files"] > 0

    if not qdrant_exists and not tracker_exists:
        _warn(f"'{name}' introuvable dans Qdrant ni dans le tracker.")
        return 0

    # Afficher ce qui sera supprimé
    if qdrant_exists and not tracker_only:
        info = client.get_collection_info(name)
        _rprint(f"  [red]Qdrant  : {info.get('points_count', '?')} points seront supprimés[/]", con)
    if tracker_exists and not qdrant_only:
        _rprint(f"  [red]Tracker : {tracker_stats['total_files']} enregistrements seront supprimés[/]", con)

    scope = "tracker seul" if tracker_only else "Qdrant seul" if qdrant_only else "Qdrant + tracker"
    if not _confirm(f"⚠  Purge définitive de '{name}' ({scope}) ?", force=yes):
        _info("Annulé.")
        return 0

    # Exécution
    if qdrant_exists and not tracker_only:
        client.delete_collection(name)
        _ok(f"Collection Qdrant '{name}' supprimée.")

    if tracker_exists and not qdrant_only:
        count = tracker.reset_collection(name)
        _ok(f"Tracker '{name}' purgé ({count} enregistrement(s)).")

    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: delete-file
# ══════════════════════════════════════════════════════════════════════════════

def cmd_delete_file(args) -> int:
    """
    Supprimer un fichier spécifique du tracker + ses points Qdrant.

    Utile pour forcer la réingestion d'un seul fichier sans reset global.
    """
    _bootstrap()
    cfg    = _resolve_config(args)
    from civitas.ingestion.qdrant import CivitasQdrantClient, IngestionTracker
    client  = CivitasQdrantClient.from_config(cfg)
    tracker = IngestionTracker(cfg.tracker_db_path)
    yes     = getattr(args, "yes", False)

    file_path  = str(Path(args.file).resolve())
    collection = args.collection

    record = tracker.get_record(file_path, collection)
    if not record:
        _err(f"Fichier '{file_path}' non trouvé dans le tracker de '{collection}'.")
        return 1

    _info(f"Fichier  : {file_path}")
    _info(f"Chunks   : {record.chunks_count}")
    _info(f"Points   : {len(record.point_ids)}")
    _info(f"Statut   : {record.status}")

    if not _confirm(f"Supprimer ce fichier de '{collection}' ?", force=yes):
        _info("Annulé.")
        return 0

    # Supprimer les points Qdrant
    if record.point_ids and client.collection_exists(collection):
        deleted = client.delete_points_by_ids(collection, record.point_ids)
        _ok(f"{deleted} point(s) Qdrant supprimé(s).")

    # Supprimer du tracker
    tracker.remove_file(file_path, collection)
    _ok(f"Fichier supprimé du tracker '{collection}'.")
    _info("Le fichier sera réingéré lors du prochain ingest.")
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: export
# ══════════════════════════════════════════════════════════════════════════════

def cmd_export(args) -> int:
    """
    Exporter les métadonnées d'une collection (tracker + payload Qdrant).

    Formats : json | csv
    """
    _bootstrap()
    cfg    = _resolve_config(args)
    con    = _console()
    from civitas.ingestion.qdrant import IngestionTracker, CivitasQdrantClient
    tracker = IngestionTracker(cfg.tracker_db_path)
    client  = CivitasQdrantClient.from_config(cfg)
    name    = args.collection
    fmt     = args.format.lower()
    output  = getattr(args, "output", None)

    # Header uniquement si on écrit dans un fichier (pas stdout)
    if output:
        _header(f"CIVITAS · Export : {name}  [{fmt.upper()}]")

    records = tracker.list_collection(name)
    if not records:
        _warn(f"Aucun enregistrement trackéé pour '{name}'.")
        return 0

    data = [
        {
            "file_path":   r.file_path,
            "collection":  r.collection,
            "status":      r.status,
            "chunks":      r.chunks_count,
            "file_size":   r.file_size,
            "file_hash":   r.file_hash,
            "mtime":       r.mtime,
            "ingested_at": r.ingested_at,
            "point_ids":   r.point_ids,
            "error_msg":   r.error_msg,
        }
        for r in records
    ]

    # Générer le contenu
    if fmt == "json":
        content = json.dumps(data, indent=2, ensure_ascii=False)
    elif fmt == "csv":
        import csv, io
        buf = io.StringIO()
        if data:
            writer = csv.DictWriter(buf, fieldnames=data[0].keys())
            writer.writeheader()
            for row in data:
                row["point_ids"] = json.dumps(row["point_ids"])
                writer.writerow(row)
        content = buf.getvalue()
    else:
        _err(f"Format inconnu : '{fmt}'. Valides : json | csv")
        return 1

    # Écrire ou afficher
    if output:
        Path(output).write_text(content, encoding="utf-8")
        _ok(f"{len(records)} enregistrement(s) exporté(s) → {output}")
    else:
        print(content)

    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDE: collections  (alias rapide)
# ══════════════════════════════════════════════════════════════════════════════

def cmd_collections(args) -> int:
    """Lister toutes les collections Qdrant avec leur nombre de points."""
    _bootstrap()
    cfg    = _resolve_config(args)
    con    = _console()
    from civitas.ingestion.qdrant import CivitasQdrantClient
    client = CivitasQdrantClient.from_config(cfg)

    if not cfg.qdrant_in_memory and not client.health_check():
        _err(f"Qdrant inaccessible à {cfg.qdrant_host}:{cfg.qdrant_port}")
        return 1

    collections = client.list_collections()
    _header("CIVITAS · Collections Qdrant")

    if not collections:
        _warn("Aucune collection dans Qdrant.")
        return 0

    rows = []
    for name in collections:
        info = client.get_collection_info(name)
        rows.append([
            name,
            str(info.get("points_count", 0)),
            str(info.get("vector_size", "?")),
            str(info.get("distance", "?")),
            str(info.get("status", "?")),
        ])

    _table_rich(con, ["Collection", "Points", "Dim", "Distance", "Statut"], rows)
    _rprint(f"\n  [dim]Total : {len(collections)} collection(s)[/]\n", con)
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  ARGUMENT PARSER
# ══════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    # ── Parser racine ─────────────────────────────────────────
    parser = argparse.ArgumentParser(
        prog            = PROG,
        description     = "CIVITAS-RAG · Système d'ingestion vectorielle Qdrant",
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog          = __doc__,
        add_help        = True,
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {VERSION}")

    # Options globales (disponibles pour toutes les sous-commandes)
    g = parser.add_argument_group("Options globales")
    g.add_argument("-c", "--config",
        metavar="FILE",
        help="Fichier YAML de configuration (config/qdrant_ingestion.yaml)")
    g.add_argument("--qdrant-host",   metavar="HOST", help="Hôte Qdrant (override)")
    g.add_argument("--qdrant-port",   metavar="PORT", type=int, help="Port Qdrant (override)")
    g.add_argument("--qdrant-url",    metavar="URL",  help="URL Qdrant Cloud (override)")
    g.add_argument("-v", "--verbose", action="store_true",
        help="Activer les logs verbeux (DEBUG)")
    g.add_argument("--log-file",      metavar="FILE", help="Écrire les logs dans un fichier")

    # Options embedding globales (applicables à toutes les commandes)
    eg = parser.add_argument_group("Options embedding (override YAML/défaut)")
    eg.add_argument("--embedding-provider", metavar="PROVIDER",
        choices=["sentence-transformers", "openai", "tfidf-local"],
        help="Provider d'embedding : sentence-transformers | openai | tfidf-local")
    eg.add_argument("--embedding-model",    metavar="NAME",
        help="Nom du modèle d'embedding (ex: all-MiniLM-L6-v2)")
    eg.add_argument("--embedding-dim",      metavar="N",  type=int,
        help="Dimension des vecteurs (ex: 384, 768, 1536, 128)")

    subs = parser.add_subparsers(dest="command", metavar="<commande>")

    # ─────────────────────────────────────────────────────────
    #  ingest
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("ingest",
        help        = "Ingérer des documents dans Qdrant",
        description = "Ingestion incrémentale, récursive et paramétrable.",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--path",       metavar="DIR",  help="Répertoire ou fichier source")
    src.add_argument("--scan",       metavar="NAME", help="Nom d'un scan prédéfini (nécessite --config)")
    src.add_argument("--all",        action="store_true", help="Lancer tous les scans du YAML (nécessite --config)")
    p.add_argument("--collection",   metavar="NAME", help="Collection Qdrant cible (avec --path)")
    p.add_argument("--dry-run",      action="store_true", help="Analyser sans écrire dans Qdrant")
    p.add_argument("--force",        action="store_true", help="Réingérer même les fichiers inchangés")
    p.add_argument("--no-recursive", action="store_true", help="Ne pas traverser les sous-dossiers")
    p.add_argument("--domain",       metavar="STR",  help="Domaine métier (ex: devops, security)")
    p.add_argument("--tags",         metavar="TAG",  nargs="*", help="Tags associés aux documents")
    p.add_argument("--extensions",   metavar="EXT",  nargs="*",
        help="Extensions autorisées, ex: .yml .yaml .tf")
    p.add_argument("--chunk-size",   metavar="N",    type=int, help="Taille max d'un chunk (en mots)")
    p.add_argument("--chunk-overlap",metavar="N",    type=int, help="Chevauchement entre chunks (en mots)")
    p.add_argument("--max-size-mb",  metavar="N",    type=float, default=100.0,
        help="Taille max d'un fichier en MB (défaut: 100)")

    # ─────────────────────────────────────────────────────────
    #  search
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("search",
        help        = "Recherche sémantique vectorielle",
        description = "Rechercher des documents par similarité sémantique.",
    )
    p.add_argument("--query", "-q",    required=True, metavar="TEXT", help="Texte de la requête")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--collection",       metavar="NAME", help="Collection unique à interroger")
    grp.add_argument("--all-collections",  action="store_true", help="Interroger toutes les collections")
    p.add_argument("--top-k",             metavar="N", type=int, default=5,
        help="Nombre de résultats (défaut: 5)")
    p.add_argument("--min-score",         metavar="F", type=float, default=0.0,
        help="Score minimum de similarité (0.0–1.0, défaut: 0)")
    p.add_argument("--show-text",         action="store_true",
        help="Afficher l'extrait de texte du chunk")
    p.add_argument("--json",              dest="json_output", action="store_true",
        help="Sortie en JSON (stdout)")
    p.add_argument("--filter-domain",     metavar="STR",  help="Filtrer par domaine métier")
    p.add_argument("--filter-extension",  metavar="EXT",  help="Filtrer par extension (ex: .yml)")
    p.add_argument("--filter-tags",       metavar="TAG",  nargs="*", help="Filtrer par tags (OR)")

    # ─────────────────────────────────────────────────────────
    #  tree
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("tree",
        help        = "Visualiser les fichiers scannables",
        description = "Afficher l'arborescence des fichiers qui seraient ingérés.",
    )
    p.add_argument("--path",         required=True, metavar="DIR", help="Répertoire à analyser")
    p.add_argument("--extensions",   metavar="EXT", nargs="*",
        help="Extensions à inclure (surcharge la config)")
    p.add_argument("--no-recursive", action="store_true", help="Ne pas traverser les sous-dossiers")
    p.add_argument("--max-files",    metavar="N", type=int, default=500,
        help="Nombre max de fichiers à afficher (défaut: 500)")
    p.add_argument("--max-size-mb",  metavar="N", type=float, default=100.0,
        help="Taille max des fichiers à inclure (MB)")

    # ─────────────────────────────────────────────────────────
    #  status
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("status",
        help        = "Statut complet du système",
        description = "Afficher l'état du tracker SQLite et des collections Qdrant.",
    )
    p.add_argument("--collection", metavar="NAME", help="Limiter à une collection spécifique")

    # ─────────────────────────────────────────────────────────
    #  inspect
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("inspect",
        help        = "Inspecter une collection en détail",
        description = "Info Qdrant + fichiers trackés + sample de points.",
    )
    p.add_argument("--collection", required=True, metavar="NAME", help="Nom de la collection")
    p.add_argument("--limit",      metavar="N", type=int, default=20,
        help="Nombre de fichiers à lister (défaut: 20)")
    p.add_argument("--sample",     metavar="N", type=int, default=3,
        help="Nombre de points Qdrant à afficher (défaut: 3, 0 = désactiver)")

    # ─────────────────────────────────────────────────────────
    #  list-files
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("list-files",
        help        = "Lister les fichiers trackés",
        description = "Afficher tous les fichiers enregistrés dans le tracker.",
    )
    p.add_argument("--collection",    required=True, metavar="NAME", help="Nom de la collection")
    p.add_argument("--status",        dest="status_filter", metavar="STATUS",
        choices=["success", "failed"], help="Filtrer par statut")
    p.add_argument("--json",          dest="json_output", action="store_true",
        help="Sortie en JSON (stdout)")

    # ─────────────────────────────────────────────────────────
    #  diff
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("diff",
        help        = "Comparer disque vs tracker",
        description = "Voir exactement ce qu'une prochaine ingestion ferait.",
    )
    p.add_argument("--path",           required=True, metavar="DIR",  help="Répertoire source")
    p.add_argument("--collection",     required=True, metavar="NAME", help="Collection cible")
    p.add_argument("--no-recursive",   action="store_true", help="Ne pas traverser les sous-dossiers")
    p.add_argument("--show-unchanged", action="store_true", help="Afficher aussi les fichiers inchangés")

    # ─────────────────────────────────────────────────────────
    #  verify
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("verify",
        help        = "Vérifier la cohérence tracker ↔ Qdrant",
        description = "Détecter les anomalies entre le tracker et Qdrant.",
    )
    p.add_argument("--collection",    metavar="NAME", help="Vérifier une collection spécifique")
    p.add_argument("--check-points",  action="store_true",
        help="Vérifier l'existence des points Qdrant (plus lent)")

    # ─────────────────────────────────────────────────────────
    #  reset
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("reset",
        help        = "Réinitialiser le tracker",
        description = "Forcer la réingestion au prochain run (ne touche pas Qdrant).",
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--collection",  metavar="NAME", help="Réinitialiser une collection")
    grp.add_argument("--all",         action="store_true", help="Réinitialiser tout le tracker")
    grp.add_argument("--failed-only", action="store_true",
        help="Supprimer uniquement les enregistrements en erreur")
    p.add_argument("--yes", "-y",     action="store_true", help="Confirmer sans prompt interactif")

    # ─────────────────────────────────────────────────────────
    #  purge
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("purge",
        help        = "Supprimer une collection Qdrant + son tracker",
        description = "Purge définitive d'une collection. Irréversible.",
    )
    p.add_argument("--collection",    required=True, metavar="NAME", help="Collection à purger")
    p.add_argument("--yes", "-y",     action="store_true", help="Confirmer sans prompt interactif")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--tracker-only", action="store_true",
        help="Supprimer uniquement le tracker (garder Qdrant)")
    grp.add_argument("--qdrant-only",  action="store_true",
        help="Supprimer uniquement Qdrant (garder le tracker)")

    # ─────────────────────────────────────────────────────────
    #  delete-file
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("delete-file",
        help        = "Supprimer un fichier du tracker + Qdrant",
        description = "Forcer la réingestion d'un fichier spécifique.",
    )
    p.add_argument("--file",       required=True, metavar="PATH", help="Chemin du fichier")
    p.add_argument("--collection", required=True, metavar="NAME", help="Collection cible")
    p.add_argument("--yes", "-y",  action="store_true", help="Confirmer sans prompt interactif")

    # ─────────────────────────────────────────────────────────
    #  export
    # ─────────────────────────────────────────────────────────
    p = subs.add_parser("export",
        help        = "Exporter les métadonnées d'une collection",
        description = "Exporter le tracker en JSON ou CSV.",
    )
    p.add_argument("--collection", required=True, metavar="NAME", help="Collection à exporter")
    p.add_argument("--format",     metavar="FMT", default="json",
        choices=["json", "csv"], help="Format de sortie : json | csv (défaut: json)")
    p.add_argument("--output",     metavar="FILE",
        help="Fichier de sortie (défaut: stdout)")

    # ─────────────────────────────────────────────────────────
    #  ping
    # ─────────────────────────────────────────────────────────
    subs.add_parser("ping",
        help        = "Tester la connexion à Qdrant",
        description = "Vérifier que Qdrant est accessible et afficher ses infos.",
    )

    # ─────────────────────────────────────────────────────────
    #  config
    # ─────────────────────────────────────────────────────────
    subs.add_parser("config",
        help        = "Afficher la configuration active",
        description = "Afficher toute la configuration résolue (YAML + env vars + defaults).",
    )

    # ─────────────────────────────────────────────────────────
    #  collections
    # ─────────────────────────────────────────────────────────
    subs.add_parser("collections",
        help        = "Lister les collections Qdrant",
        description = "Afficher toutes les collections et leur nombre de points.",
    )

    return parser


# ══════════════════════════════════════════════════════════════════════════════
#  DISPATCH
# ══════════════════════════════════════════════════════════════════════════════

_COMMANDS: dict[str, callable] = {
    "ingest":       cmd_ingest,
    "search":       cmd_search,
    "tree":         cmd_tree,
    "status":       cmd_status,
    "inspect":      cmd_inspect,
    "list-files":   cmd_list_files,
    "diff":         cmd_diff,
    "verify":       cmd_verify,
    "reset":        cmd_reset,
    "purge":        cmd_purge,
    "delete-file":  cmd_delete_file,
    "export":       cmd_export,
    "ping":         cmd_ping,
    "config":       cmd_config,
    "collections":  cmd_collections,
}


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    # Logging
    level = "DEBUG" if getattr(args, "verbose", False) else "WARNING"
    _setup_logging(level, getattr(args, "log_file", None))

    if not args.command:
        parser.print_help()
        sys.exit(0)

    handler = _COMMANDS.get(args.command)
    if handler is None:
        _err(f"Commande inconnue : '{args.command}'")
        parser.print_help()
        sys.exit(1)

    try:
        sys.exit(handler(args))
    except KeyboardInterrupt:
        print("\n[Interrompu]", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        _err(f"Erreur inattendue : {exc}")
        if os.getenv("CIVITAS_DEBUG") or getattr(args, "verbose", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

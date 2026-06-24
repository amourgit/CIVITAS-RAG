#!/usr/bin/env python3
"""
scripts/ingest.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLI entry point for document ingestion.

Usage:
    # Ingest a single file
    python scripts/ingest.py --path /data/docs/contract.pdf \\
        --space legal-contracts --domain legal --category contracts

    # Ingest a directory
    python scripts/ingest.py --path /data/docs/legal/ \\
        --space legal-contracts --domain legal --category contracts \\
        --team legal-team

    # Dry run (parse + chunk but don't index)
    python scripts/ingest.py --path /data/docs/ --space legal-contracts \\
        --domain legal --category contracts --dry-run

    # Auto-approve high-quality documents
    python scripts/ingest.py --path /data/docs/ --space hr-policies \\
        --domain hr --category policies --auto-approve

    # With taxonomy path
    python scripts/ingest.py --path /data/docs/nda.pdf \\
        --space legal-contracts --domain legal --category contracts \\
        --taxonomy legal.contracts.nda
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("civitas.ingest")


def build_pipeline() -> object:
    """Construct the ingestion pipeline with all dependencies."""
    from dotenv import load_dotenv
    load_dotenv()

    from civitas.ingestion.connectors.filesystem import FilesystemConnector, ConnectorConfig
    from civitas.ingestion.parsers.text import ParserRegistry
    from civitas.ingestion.transformers.chunker import SentenceChunker
    from civitas.ingestion.transformers.enricher import DocumentEnricher
    from civitas.ingestion.pipeline import IngestionPipeline

    return IngestionPipeline(
        parser_registry=ParserRegistry(),
        enricher=DocumentEnricher(),
        chunker=SentenceChunker(
            chunk_size=int(os.getenv("INGESTION_CHUNK_SIZE", "512")),
            chunk_overlap=int(os.getenv("INGESTION_CHUNK_OVERLAP", "64")),
        ),
        # index_manager and doc_store injected here when databases are available
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CIVITAS Document Ingestion CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--path", required=True, help="File or directory path to ingest")
    parser.add_argument("--space", required=True, help="Target knowledge space name")
    parser.add_argument("--domain", required=True, help="Business domain (e.g. legal, finance)")
    parser.add_argument("--category", required=True, help="Document category (e.g. contracts)")
    parser.add_argument("--taxonomy", default="",
                        help="Taxonomy path (dot-separated, e.g. legal.contracts.nda)")
    parser.add_argument("--owner", default="system", help="Owner user ID")
    parser.add_argument("--team", default="", help="Owning team ID")
    parser.add_argument("--allowed-teams", nargs="*", default=[],
                        help="Teams allowed to read ingested documents")
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--chunk-overlap", type=int, default=64)
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-approve documents meeting quality threshold")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and chunk but do not index or persist")
    parser.add_argument("--min-quality", type=float, default=0.5)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate path
    target_path = Path(args.path)
    if not target_path.exists():
        logger.error("Path does not exist: %s", args.path)
        sys.exit(1)

    # Discover files
    if target_path.is_file():
        files = [target_path]
    else:
        from civitas.ingestion.connectors.filesystem import FilesystemConnector, ConnectorConfig
        connector = FilesystemConnector(
            config=ConnectorConfig(connector_id="cli-ingest"),
            watch_dirs=[str(target_path)],
        )
        files_str = list(connector.discover())
        files = [Path(f) for f in files_str]

    logger.info("Found %d file(s) to ingest from '%s'", len(files), args.path)
    if not files:
        logger.warning("No files found. Check path and allowed extensions.")
        sys.exit(0)

    # Build pipeline
    pipeline = build_pipeline()

    # Build pipeline config
    from uuid import uuid4
    from civitas.ingestion.pipeline import PipelineConfig
    config = PipelineConfig(
        knowledge_space_id=uuid4(),   # In production, resolve from registry
        knowledge_space_name=args.space,
        domain=args.domain,
        category=args.category,
        taxonomy_path=args.taxonomy.split(".") if args.taxonomy else [args.domain, args.category],
        owner_id=args.owner,
        team_id=args.team or None,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        min_quality_score=args.min_quality,
        auto_approve=args.auto_approve,
        auto_approve_threshold=0.85,
        allowed_teams=args.allowed_teams,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        logger.info("[DRY RUN] Pipeline will parse and chunk but NOT index or persist.")

    # Process files
    from civitas.ingestion.connectors.filesystem import FilesystemConnector, ConnectorConfig
    connector = FilesystemConnector(
        config=ConnectorConfig(connector_id="cli-ingest"),
        watch_dirs=[str(target_path) if target_path.is_dir() else str(target_path.parent)],
    )

    blobs = []
    for f in files:
        try:
            blob = connector.fetch(str(f))
            blobs.append(blob)
        except Exception as exc:
            logger.warning("Could not fetch '%s': %s", f, exc)

    if not blobs:
        logger.error("No files could be fetched.")
        sys.exit(1)

    t0 = time.monotonic()
    stats = pipeline.process_many(blobs, config)
    elapsed = time.monotonic() - t0

    # Report
    print("\n" + "═" * 60)
    print("  CIVITAS INGESTION REPORT")
    print("═" * 60)
    print(f"  Files processed : {stats.total_processed}")
    print(f"  Succeeded       : {stats.total_succeeded}")
    print(f"  Failed          : {stats.total_failed}")
    print(f"  Total chunks    : {stats.total_chunks}")
    print(f"  Success rate    : {stats.success_rate:.0%}")
    print(f"  Total duration  : {elapsed:.1f}s")
    print(f"  Dry run         : {'YES' if args.dry_run else 'NO'}")
    if stats.errors:
        print(f"\n  Errors ({len(stats.errors)}):")
        for err in stats.errors[:10]:
            print(f"    · {err}")
    print("═" * 60 + "\n")

    sys.exit(0 if stats.total_failed == 0 else 1)


if __name__ == "__main__":
    main()

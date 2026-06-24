#!/usr/bin/env python3
"""
scripts/setup_database.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Initialize the CIVITAS PostgreSQL database.

Runs all migrations in order and creates vector tables
for each default knowledge space.

Usage:
    python scripts/setup_database.py
    python scripts/setup_database.py --dry-run
    python scripts/setup_database.py --reset   # Drop and recreate (DESTRUCTIVE)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Add parent to sys.path so we can import civitas
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent.parent / "database" / "migrations"
MIGRATION_FILES = [
    "001_core_schema.sql",
    "002_vector_extensions.sql",
    "003_graph_schema.sql",
    "004_governance_schema.sql",
]


def get_connection_url() -> str:
    """Build PostgreSQL connection URL from environment."""
    from dotenv import load_dotenv
    load_dotenv()
    return (
        f"postgresql://"
        f"{os.getenv('POSTGRES_USER', 'civitas')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'change_me')}@"
        f"{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/"
        f"{os.getenv('POSTGRES_DB', 'civitas_knowledge')}"
    )


def run_migration(conn_url: str, sql_file: Path, dry_run: bool = False) -> None:
    """Execute a single migration file."""
    logger.info("Running migration: %s", sql_file.name)
    sql = sql_file.read_text()
    if dry_run:
        logger.info("[DRY RUN] Would execute %d chars of SQL", len(sql))
        return
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(conn_url)
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
        logger.info("✓ Migration complete: %s", sql_file.name)
    except Exception as exc:
        logger.error("✗ Migration failed: %s → %s", sql_file.name, exc)
        raise


def create_vector_tables(conn_url: str, dry_run: bool = False) -> None:
    """Create pgvector tables for all default knowledge spaces."""
    from civitas.knowledge.spaces.registry import KnowledgeSpaceRegistry
    from civitas.storage.vector.pgvector import PgVectorSchema

    logger.info("Creating vector tables for knowledge spaces...")
    registry = KnowledgeSpaceRegistry()
    schema = PgVectorSchema(connection_url=conn_url)

    if dry_run:
        for space in registry.list_all():
            table = f"civitas_vectors_{space.name.replace('-', '_')}"
            logger.info("[DRY RUN] Would create vector table: %s", table)
        return

    schema.ensure_extensions()
    for space in registry.list_all():
        table = f"civitas_vectors_{space.name.replace('-', '_')}"
        try:
            schema.create_vector_table(table_name=table)
            logger.info("✓ Vector table ready: %s", table)
        except Exception as exc:
            logger.warning("Could not create vector table %s: %s", table, exc)


def reset_database(conn_url: str) -> None:
    """Drop all CIVITAS tables (DESTRUCTIVE — dev only)."""
    logger.warning("RESETTING DATABASE — all CIVITAS data will be lost!")
    from sqlalchemy import create_engine, text
    engine = create_engine(conn_url)
    with engine.connect() as conn:
        conn.execute(text("""
            DROP TABLE IF EXISTS
                civitas_audit_events,
                civitas_quality_reports,
                civitas_retention_policies,
                civitas_graph_communities,
                civitas_graph_relations,
                civitas_graph_entities,
                civitas_document_chunks,
                civitas_documents,
                civitas_index_registry,
                civitas_taxonomy_nodes,
                civitas_knowledge_spaces,
                civitas_embedding_cache,
                civitas_vectors_global
            CASCADE
        """))
        # Drop dynamic vector tables
        result = conn.execute(text("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public' AND tablename LIKE 'civitas_vectors_%'
        """))
        for row in result:
            conn.execute(text(f"DROP TABLE IF EXISTS {row[0]} CASCADE"))
        conn.commit()
    logger.warning("Database reset complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="CIVITAS database setup")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables (DESTRUCTIVE)")
    parser.add_argument("--skip-vector-tables", action="store_true", help="Skip vector table creation")
    args = parser.parse_args()

    conn_url = get_connection_url()
    logger.info("Connecting to: %s", conn_url.split("@")[1] if "@" in conn_url else conn_url)

    if args.reset:
        confirm = input("Type 'RESET' to confirm database reset: ")
        if confirm != "RESET":
            logger.info("Aborted.")
            return
        reset_database(conn_url)

    # Run migrations
    for migration_name in MIGRATION_FILES:
        migration_path = MIGRATIONS_DIR / migration_name
        if not migration_path.exists():
            logger.warning("Migration file not found: %s", migration_path)
            continue
        run_migration(conn_url, migration_path, dry_run=args.dry_run)

    # Create vector tables
    if not args.skip_vector_tables:
        create_vector_tables(conn_url, dry_run=args.dry_run)

    logger.info("✓ CIVITAS database setup complete.")


if __name__ == "__main__":
    main()

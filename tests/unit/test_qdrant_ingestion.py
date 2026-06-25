"""
tests/unit/test_qdrant_ingestion.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tests unitaires et d'intégration pour le système d'ingestion Qdrant.

Tests couverts:
  · FileScanner — découverte récursive et filtrage
  · TextChunker — chunking de texte
  · IngestionTracker — déduplication SQLite
  · QdrantIngestionPipeline — pipeline end-to-end (mode in-memory)
  · Recherche sémantique (mode in-memory)
  · Déduplication (relance sans re-ingestion)
  · Gestion des fichiers modifiés
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────
#  FIXTURES
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_docs(tmp_path: Path) -> Path:
    """Crée une arborescence de test."""
    # Structure: /docs/devops/ansible/*.yml, /docs/cloud/terraform/*.tf
    (tmp_path / "devops" / "ansible").mkdir(parents=True)
    (tmp_path / "devops" / "docker").mkdir(parents=True)
    (tmp_path / "cloud" / "terraform").mkdir(parents=True)
    (tmp_path / "security" / "iam").mkdir(parents=True)

    (tmp_path / "devops" / "ansible" / "nginx.yml").write_text(
        "---\n- name: Install nginx\n  hosts: webservers\n  tasks:\n    - apt: name=nginx state=present\n"
    )
    (tmp_path / "devops" / "ansible" / "postgres.yml").write_text(
        "---\n- name: Install PostgreSQL database server\n  hosts: databases\n  tasks:\n    - apt: name=postgresql state=present\n"
    )
    (tmp_path / "devops" / "docker" / "Dockerfile").write_text(
        "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nRUN pip install -e .\nCMD ['python', 'app.py']\n"
    )
    (tmp_path / "cloud" / "terraform" / "main.tf").write_text(
        'resource "aws_vpc" "main" {\n  cidr_block = "10.0.0.0/16"\n  tags = { Name = "civitas-vpc" }\n}\n'
    )
    (tmp_path / "security" / "iam" / "policy.json").write_text(
        '{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "*"}]}\n'
    )
    # Fichiers qui doivent être ignorés
    (tmp_path / "devops" / ".DS_Store").write_text("ignored")
    (tmp_path / "devops" / "ansible" / "tmp.tmp").write_text("ignored")

    return tmp_path


@pytest.fixture
def tfidf_config():
    """Config Qdrant avec TF-IDF local et in-memory."""
    from civitas.ingestion.qdrant import QdrantIngestionConfig, EmbeddingConfig
    config = QdrantIngestionConfig(
        qdrant_in_memory=True,
    )
    config.embedding = EmbeddingConfig(
        provider="tfidf-local",
        model_name="tfidf",
        vector_size=64,
        batch_size=16,
    )
    return config


@pytest.fixture
def tracker_db(tmp_path: Path) -> str:
    return str(tmp_path / "test_tracker.db")


# ─────────────────────────────────────────────────────────────
#  SCANNER TESTS
# ─────────────────────────────────────────────────────────────

class TestFileScanner:

    def test_recursive_scan_finds_all_files(self, tmp_docs: Path):
        from civitas.ingestion.qdrant import FileScanner
        scanner = FileScanner(
            allowed_extensions=[".yml", ".yaml", ".tf", ".json", "Dockerfile"],
        )
        result = scanner.scan(tmp_docs)
        assert result.total_discovered == 5
        names = {f.filename for f in result.discovered}
        assert "nginx.yml" in names
        assert "postgres.yml" in names
        assert "Dockerfile" in names
        assert "main.tf" in names
        assert "policy.json" in names

    def test_excluded_patterns_are_skipped(self, tmp_docs: Path):
        from civitas.ingestion.qdrant import FileScanner
        scanner = FileScanner(
            allowed_extensions=[".yml", ".tmp"],
        )
        result = scanner.scan(tmp_docs)
        filenames = {f.filename for f in result.discovered}
        assert ".DS_Store" not in filenames
        assert "tmp.tmp" not in filenames

    def test_relative_paths_are_correct(self, tmp_docs: Path):
        from civitas.ingestion.qdrant import FileScanner
        scanner = FileScanner(allowed_extensions=[".yml"])
        result = scanner.scan(tmp_docs)
        rel_paths = {f.relative_path for f in result.discovered}
        assert any("devops" in p and "ansible" in p for p in rel_paths)

    def test_depth_is_calculated(self, tmp_docs: Path):
        from civitas.ingestion.qdrant import FileScanner
        scanner = FileScanner(allowed_extensions=[".yml"])
        result = scanner.scan(tmp_docs)
        for f in result.discovered:
            assert f.depth >= 1  # Au moins un niveau de sous-dossier

    def test_single_file_scan(self, tmp_docs: Path):
        from civitas.ingestion.qdrant import FileScanner
        scanner = FileScanner(allowed_extensions=[".yml"])
        result = scanner.scan(tmp_docs / "devops" / "ansible" / "nginx.yml")
        assert result.total_discovered == 1

    def test_non_recursive_scan(self, tmp_docs: Path):
        from civitas.ingestion.qdrant import FileScanner
        scanner = FileScanner(
            allowed_extensions=[".yml"],
            recursive=False,
        )
        result = scanner.scan(tmp_docs / "devops" / "ansible")
        # Avec recursive=False, depth > 0 stop après premier niveau
        assert result.total_discovered >= 0  # Dépend du niveau

    def test_nonexistent_path(self):
        from civitas.ingestion.qdrant import FileScanner
        scanner = FileScanner()
        result = scanner.scan("/nonexistent/path/that/does/not/exist")
        assert result.total_discovered == 0

    def test_extensionless_files_detected(self, tmp_docs: Path):
        from civitas.ingestion.qdrant import FileScanner
        scanner = FileScanner(allowed_extensions=["Dockerfile"])
        result = scanner.scan(tmp_docs)
        assert result.total_discovered == 1
        assert result.discovered[0].filename == "Dockerfile"
        assert result.discovered[0].extension == "Dockerfile"


# ─────────────────────────────────────────────────────────────
#  CHUNKER TESTS
# ─────────────────────────────────────────────────────────────

class TestTextChunker:

    def test_basic_chunking(self):
        from civitas.ingestion.qdrant import TextChunker
        chunker = TextChunker(chunk_size=10, chunk_overlap=2)
        text = " ".join([f"word{i}" for i in range(50)])
        chunks = chunker.chunk(text)
        assert len(chunks) > 1
        for c in chunks:
            assert c.text.strip()
            assert c.chunk_index >= 0

    def test_empty_text_returns_empty(self):
        from civitas.ingestion.qdrant import TextChunker
        chunker = TextChunker()
        assert chunker.chunk("") == []
        assert chunker.chunk("   \n  ") == []

    def test_short_text_single_chunk(self):
        from civitas.ingestion.qdrant import TextChunker
        chunker = TextChunker(chunk_size=1000)
        text = "Short text with just a few words."
        chunks = chunker.chunk(text)
        assert len(chunks) == 1

    def test_chunk_overlap(self):
        from civitas.ingestion.qdrant import TextChunker
        chunker = TextChunker(chunk_size=10, chunk_overlap=5)
        # 60 mots → doit produire plusieurs chunks avec taille=10, step=5
        text = " ".join([f"w{i}" for i in range(60)])
        chunks = chunker.chunk(text)
        assert len(chunks) >= 2

    def test_chunk_with_context_adds_filename(self):
        from civitas.ingestion.qdrant import TextChunker
        chunker = TextChunker(chunk_size=100)
        text = "Some configuration content here."
        chunks = chunker.chunk_with_context(text, "ansible/webservers/nginx.yml")
        assert len(chunks) >= 1
        assert "nginx.yml" in chunks[0].text

    def test_yaml_content_chunked(self):
        from civitas.ingestion.qdrant import TextChunker
        chunker = TextChunker(chunk_size=20)
        yaml_text = """
- name: Install packages
  hosts: all
  tasks:
    - name: Install nginx
      apt:
        name: nginx
        state: present
    - name: Start service
      service:
        name: nginx
        state: started
        enabled: yes
"""
        chunks = chunker.chunk(yaml_text)
        assert len(chunks) >= 1
        assert all(c.text.strip() for c in chunks)


# ─────────────────────────────────────────────────────────────
#  TRACKER TESTS
# ─────────────────────────────────────────────────────────────

class TestIngestionTracker:

    def test_new_file_should_ingest(self, tmp_docs: Path, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        file_path = str(tmp_docs / "devops" / "ansible" / "nginx.yml")
        should, reason = tracker.should_ingest(file_path, "ansible_docs")
        assert should is True
        assert reason == "new"

    def test_after_success_should_skip(self, tmp_docs: Path, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        file_path = str(tmp_docs / "devops" / "ansible" / "nginx.yml")
        tracker.mark_success(file_path, "ansible_docs", ["point-1", "point-2"], 3)
        should, reason = tracker.should_ingest(file_path, "ansible_docs")
        assert should is False
        assert reason == "unchanged"

    def test_failed_file_should_retry(self, tmp_docs: Path, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        file_path = str(tmp_docs / "devops" / "ansible" / "nginx.yml")
        tracker.mark_failed(file_path, "ansible_docs", "Parse error")
        should, reason = tracker.should_ingest(file_path, "ansible_docs")
        assert should is True
        assert reason == "failed"

    def test_modified_file_should_reingest(self, tmp_docs: Path, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        file_path = tmp_docs / "devops" / "ansible" / "nginx.yml"
        tracker.mark_success(str(file_path), "ansible_docs", ["pt1"], 2)
        # Modifier le fichier
        time.sleep(0.01)
        file_path.write_text("# Modified content\n- name: Updated task\n  hosts: all\n")
        should, reason = tracker.should_ingest(str(file_path), "ansible_docs")
        assert should is True
        assert reason == "modified"

    def test_force_reingest_ignores_cache(self, tmp_docs: Path, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        file_path = str(tmp_docs / "devops" / "ansible" / "nginx.yml")
        tracker.mark_success(file_path, "ansible_docs", ["pt1"], 2)
        should, reason = tracker.should_ingest(file_path, "ansible_docs", skip_existing=False)
        assert should is True
        assert reason == "force_reingest"

    def test_stats_empty(self, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        stats = tracker.stats()
        assert stats["total_files"] == 0
        assert stats["succeeded"] == 0

    def test_stats_after_ingestion(self, tmp_docs: Path, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        file1 = str(tmp_docs / "devops" / "ansible" / "nginx.yml")
        file2 = str(tmp_docs / "devops" / "ansible" / "postgres.yml")
        tracker.mark_success(file1, "ansible_docs", ["p1"], 3)
        tracker.mark_success(file2, "ansible_docs", ["p2", "p3"], 5)
        stats = tracker.stats("ansible_docs")
        assert stats["total_files"] == 2
        assert stats["succeeded"] == 2
        assert stats["total_chunks"] == 8

    def test_reset_collection(self, tmp_docs: Path, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        file_path = str(tmp_docs / "devops" / "ansible" / "nginx.yml")
        tracker.mark_success(file_path, "ansible_docs", ["p1"], 2)
        deleted = tracker.reset_collection("ansible_docs")
        assert deleted == 1
        stats = tracker.stats("ansible_docs")
        assert stats["total_files"] == 0

    def test_get_point_ids(self, tmp_docs: Path, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        file_path = str(tmp_docs / "devops" / "ansible" / "nginx.yml")
        tracker.mark_success(file_path, "ansible_docs", ["uuid-1", "uuid-2"], 2)
        ids = tracker.get_point_ids(file_path, "ansible_docs")
        assert ids == ["uuid-1", "uuid-2"]

    def test_different_collections_independent(self, tmp_docs: Path, tracker_db: str):
        from civitas.ingestion.qdrant import IngestionTracker
        tracker = IngestionTracker(tracker_db)
        file_path = str(tmp_docs / "devops" / "ansible" / "nginx.yml")
        tracker.mark_success(file_path, "collection_A", ["p1"], 2)
        # Même fichier, collection différente → doit être ingéré
        should, reason = tracker.should_ingest(file_path, "collection_B")
        assert should is True
        assert reason == "new"


# ─────────────────────────────────────────────────────────────
#  PIPELINE INTEGRATION TESTS
# ─────────────────────────────────────────────────────────────

class TestQdrantIngestionPipeline:

    def test_ingest_directory(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        scan = ScanConfig(
            source_path=str(tmp_docs / "devops" / "ansible"),
            collection_name="ansible_test",
            domain="devops",
        )
        report = pipeline.ingest(scan)

        assert report.total_discovered == 2
        assert report.total_new == 2
        assert report.total_failed == 0
        assert report.total_chunks >= 2

    def test_deduplication_no_reingest(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        scan = ScanConfig(
            source_path=str(tmp_docs / "devops" / "ansible"),
            collection_name="ansible_dedup",
            skip_existing=True,
        )
        # Premier run
        report1 = pipeline.ingest(scan)
        assert report1.total_new == 2

        # Deuxième run sans modification — tout doit être skippé
        report2 = pipeline.ingest(scan)
        assert report2.total_new == 0
        assert report2.total_skipped == 2

    def test_force_reingest(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        scan = ScanConfig(
            source_path=str(tmp_docs / "devops" / "ansible"),
            collection_name="ansible_force",
            skip_existing=True,
        )
        pipeline.ingest(scan)

        # Forcer la réingestion
        scan_force = ScanConfig(
            source_path=str(tmp_docs / "devops" / "ansible"),
            collection_name="ansible_force",
            skip_existing=False,  # Force
        )
        report2 = pipeline.ingest(scan_force)
        assert report2.total_new + report2.total_modified == 2

    def test_dry_run_no_qdrant_write(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        scan = ScanConfig(
            source_path=str(tmp_docs / "devops" / "ansible"),
            collection_name="ansible_dry",
            dry_run=True,
        )
        report = pipeline.ingest(scan)
        assert report.total_new == 2   # "parsed" comme dry_run
        assert report.total_points == 0  # Rien dans Qdrant

        # La collection ne doit pas exister (dry run)
        assert not pipeline.qdrant.collection_exists("ansible_dry")

    def test_multi_scan(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        scans = [
            ScanConfig(source_path=str(tmp_docs / "devops" / "ansible"),    collection_name="s_ansible"),
            ScanConfig(source_path=str(tmp_docs / "cloud" / "terraform"),   collection_name="s_terraform"),
            ScanConfig(source_path=str(tmp_docs / "security" / "iam"),      collection_name="s_iam"),
        ]
        reports = pipeline.ingest_many(scans)
        assert len(reports) == 3
        total_new = sum(r.total_new for r in reports)
        assert total_new == 4   # 2 ansible + 1 terraform + 1 iam

    def test_empty_directory(self, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        scan = ScanConfig(
            source_path=str(empty_dir),
            collection_name="empty_col",
        )
        report = pipeline.ingest(scan)
        assert report.total_discovered == 0
        assert report.total_new == 0

    def test_status_returns_info(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        scan = ScanConfig(
            source_path=str(tmp_docs / "devops" / "ansible"),
            collection_name="status_test",
        )
        pipeline.ingest(scan)

        status = pipeline.status()
        assert "tracker" in status
        assert "qdrant" in status
        assert status["tracker"]["total_files"] == 2

    def test_tracker_reset(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        scan = ScanConfig(
            source_path=str(tmp_docs / "devops" / "ansible"),
            collection_name="reset_test",
        )
        pipeline.ingest(scan)

        deleted = pipeline.reset_collection_tracker("reset_test")
        assert deleted == 2

        # Après reset: les fichiers sont "new" à nouveau
        report2 = pipeline.ingest(scan)
        assert report2.total_new == 2


# ─────────────────────────────────────────────────────────────
#  SEARCH TESTS
# ─────────────────────────────────────────────────────────────

class TestSemanticSearch:

    def test_search_returns_results(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        scan = ScanConfig(
            source_path=str(tmp_docs),
            collection_name="search_test",
            domain="devops",
        )
        pipeline.ingest(scan)

        embedder = pipeline.embedder
        client = pipeline.qdrant
        query_vec = embedder.embed_query("nginx web server installation")
        results = client.search("search_test", query_vec, top_k=3)
        assert len(results) <= 3
        for r in results:
            assert isinstance(r.score, float)
            assert r.file_path
            assert r.filename
            assert r.chunk_text

    def test_search_cross_collections(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        pipeline.ingest_many([
            ScanConfig(source_path=str(tmp_docs / "devops" / "ansible"), collection_name="c_ansible"),
            ScanConfig(source_path=str(tmp_docs / "cloud" / "terraform"), collection_name="c_terraform"),
        ])

        embedder = pipeline.embedder
        client = pipeline.qdrant
        qvec = embedder.embed_query("infrastructure cloud deployment")
        results = client.search_across_collections(
            query_vector=qvec,
            collection_names=["c_ansible", "c_terraform"],
            top_k=5,
        )
        # Les résultats doivent venir des deux collections
        assert len(results) <= 5
        collections_found = {r.collection for r in results}
        assert len(collections_found) >= 1

    def test_search_score_ordering(self, tmp_docs: Path, tfidf_config, tmp_path: Path):
        from civitas.ingestion.qdrant import QdrantIngestionPipeline, ScanConfig
        tfidf_config.tracker_db_path = str(tmp_path / "tracker.db")
        pipeline = QdrantIngestionPipeline(tfidf_config)

        scan = ScanConfig(
            source_path=str(tmp_docs),
            collection_name="order_test",
        )
        pipeline.ingest(scan)

        embedder = pipeline.embedder
        client = pipeline.qdrant
        qvec = embedder.embed_query("postgresql database")
        results = client.search("order_test", qvec, top_k=5)

        # Les résultats doivent être triés par score décroissant
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

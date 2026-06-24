"""
civitas.ingestion.connectors.filesystem
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Filesystem connector — reads documents from local directories.

Supports:
  · Recursive directory traversal
  · Glob pattern filtering
  · Incremental ingestion via mtime comparison
  · Large file detection and rejection
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import chardet

from civitas.core.models.metadata import SourceType
from civitas.ingestion.connectors.base import (
    BaseConnector,
    ConnectorConfig,
    RawDocumentBlob,
)

logger = logging.getLogger(__name__)


class FilesystemConnector(BaseConnector):
    """
    Reads documents from one or more local filesystem directories.

    Usage:
        connector = FilesystemConnector(
            config=ConnectorConfig(connector_id="fs-legal"),
            watch_dirs=["/data/documents/legal"],
        )
        for blob in connector.stream():
            pipeline.process(blob)
    """

    def __init__(
        self,
        config: ConnectorConfig,
        watch_dirs: list[str | Path],
        last_run_at: Optional[datetime] = None,
    ) -> None:
        super().__init__(config)
        self.watch_dirs = [Path(d) for d in watch_dirs]
        self.last_run_at = last_run_at   # For incremental runs

    def discover(self) -> Iterator[str]:
        """Yield file paths for all discoverable documents."""
        for base_dir in self.watch_dirs:
            if not base_dir.exists():
                logger.warning("Watch directory does not exist: %s", base_dir)
                continue

            glob = "**/*" if self.config.recursive else "*"
            for file_path in sorted(base_dir.glob(glob)):
                if not file_path.is_file():
                    continue
                if not self.config.is_allowed(file_path.name):
                    continue
                if not self.config.follow_symlinks and file_path.is_symlink():
                    continue
                stat = file_path.stat()
                if stat.st_size > self.config.max_file_size_bytes:
                    logger.warning(
                        "Skipping oversized file (%d bytes > %d limit): %s",
                        stat.st_size, self.config.max_file_size_bytes, file_path,
                    )
                    continue
                # Incremental: skip files not modified since last run
                if self.last_run_at:
                    mtime = datetime.fromtimestamp(stat.st_mtime)
                    if mtime <= self.last_run_at:
                        continue
                yield str(file_path)

    def fetch(self, identifier: str) -> RawDocumentBlob:
        """Read a single file from disk and return a RawDocumentBlob."""
        file_path = Path(identifier)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {identifier}")

        content_bytes = file_path.read_bytes()
        # Detect encoding for text-based formats
        detected_encoding = None
        if file_path.suffix.lower() in (".txt", ".md", ".html", ".htm", ".csv", ".xml"):
            detection = chardet.detect(content_bytes[:4096])
            detected_encoding = detection.get("encoding")

        return RawDocumentBlob(
            content_bytes=content_bytes,
            filename=file_path.name,
            file_extension=file_path.suffix.lower(),
            source_type=SourceType.FILESYSTEM,
            source_path=str(file_path),
            detected_encoding=detected_encoding,
            extra={"mtime": os.path.getmtime(file_path)},
        )

    def health_check(self) -> bool:
        """Return True if at least one watch directory is readable."""
        return any(d.is_dir() and os.access(d, os.R_OK) for d in self.watch_dirs)

"""
civitas.ingestion.connectors.base
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Abstract base connector for all document sources.

A Connector is responsible for discovering and fetching raw document
bytes from a specific source system (filesystem, S3, API, database…).
It does NOT parse content — that is the Parser's responsibility.

Each connector produces a stream of RawDocumentBlob objects
that the ingestion pipeline forwards to the appropriate parser.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Iterator, Optional

from civitas.core.models.metadata import SourceType


@dataclass
class RawDocumentBlob:
    """
    Raw, unparsed document fetched from a source.
    Carries binary content + minimal provenance metadata.
    Content parsing is delegated to parsers.
    """
    content_bytes: bytes
    filename: str
    file_extension: str
    source_type: SourceType
    source_path: Optional[str] = None
    source_url: Optional[str] = None
    source_system: Optional[str] = None
    detected_encoding: Optional[str] = None
    byte_size: int = field(default=0)
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.byte_size = len(self.content_bytes)


@dataclass
class ConnectorConfig:
    """Configuration shared by all connectors."""
    connector_id: str
    max_file_size_bytes: int = 100 * 1024 * 1024   # 100 MB default
    allowed_extensions: list[str] = field(default_factory=lambda: [
        ".pdf", ".docx", ".doc", ".txt", ".md", ".html",
        ".htm", ".csv", ".json", ".xml", ".xlsx", ".pptx",
    ])
    excluded_patterns: list[str] = field(default_factory=lambda: [
        "*.tmp", "*.log", ".DS_Store", "~$*", "Thumbs.db",
    ])
    recursive: bool = True
    follow_symlinks: bool = False
    batch_size: int = 50
    extra: dict = field(default_factory=dict)

    def is_allowed(self, filename: str) -> bool:
        """Check if a filename should be ingested."""
        from fnmatch import fnmatch
        ext = Path(filename).suffix.lower()
        if ext not in self.allowed_extensions:
            return False
        return not any(fnmatch(filename, pat) for pat in self.excluded_patterns)


class BaseConnector(abc.ABC):
    """
    Abstract connector — base class for all source adapters.

    Subclass this to add a new document source.
    Implement `discover()` and `fetch()`.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @property
    def connector_id(self) -> str:
        return self.config.connector_id

    @abc.abstractmethod
    def discover(self) -> Iterator[str]:
        """
        Yield raw identifiers (paths, URLs, IDs) of all discoverable documents.
        Does NOT fetch content. Used for planning ingestion runs.
        """

    @abc.abstractmethod
    def fetch(self, identifier: str) -> RawDocumentBlob:
        """
        Fetch a single document by its identifier.
        Returns a RawDocumentBlob with binary content.
        """

    def stream(self) -> Iterator[RawDocumentBlob]:
        """
        Discover and fetch all documents, yielding RawDocumentBlob objects.
        Default implementation: serial discover → fetch.
        Override for source-specific batching / parallelism.
        """
        for identifier in self.discover():
            try:
                yield self.fetch(identifier)
            except Exception as exc:
                self._on_fetch_error(identifier, exc)

    def _on_fetch_error(self, identifier: str, exc: Exception) -> None:
        """
        Hook called when a fetch fails.
        Default: log and continue. Override to raise or handle differently.
        """
        import logging
        logging.getLogger(__name__).warning(
            "Failed to fetch '%s' from connector '%s': %s",
            identifier, self.connector_id, exc,
        )

    def health_check(self) -> bool:
        """Return True if the source is reachable. Override per connector."""
        return True

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id='{self.connector_id}')"

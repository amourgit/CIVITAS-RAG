"""
civitas.ingestion.parsers.base
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Abstract base parser — transforms RawDocumentBlob into plain text.

Parsers are stateless functions wrapped in a class.
Each parser handles one or more file extensions.
Parsing errors are reported as ParseResult.error, not exceptions,
so the pipeline can continue processing other documents.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional

from civitas.ingestion.connectors.base import RawDocumentBlob


@dataclass
class ParseResult:
    """
    Output of a parser.
    Contains extracted text and optional structural hints.
    """
    text: str
    page_count: Optional[int] = None
    word_count: int = 0
    character_count: int = 0
    language_hint: Optional[str] = None
    section_headings: list[str] = field(default_factory=list)
    tables_count: int = 0
    images_count: int = 0
    error: Optional[str] = None                    # Set if parsing failed
    warnings: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.error is None and bool(self.text.strip())

    def __post_init__(self) -> None:
        if self.text:
            self.word_count = len(self.text.split())
            self.character_count = len(self.text)


class BaseParser(abc.ABC):
    """
    Abstract document parser.
    Converts RawDocumentBlob → ParseResult (plain text + structural info).
    """

    @property
    @abc.abstractmethod
    def supported_extensions(self) -> list[str]:
        """List of file extensions this parser handles (lowercase, with dot)."""

    def can_parse(self, blob: RawDocumentBlob) -> bool:
        return blob.file_extension.lower() in self.supported_extensions

    @abc.abstractmethod
    def parse(self, blob: RawDocumentBlob) -> ParseResult:
        """Parse the raw document blob into a ParseResult."""

    def safe_parse(self, blob: RawDocumentBlob) -> ParseResult:
        """
        Parse with error handling.
        Returns a ParseResult with error set on failure.
        """
        try:
            return self.parse(blob)
        except Exception as exc:
            return ParseResult(
                text="",
                error=f"{self.__class__.__name__} failed: {exc}",
            )

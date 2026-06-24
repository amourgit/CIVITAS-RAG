"""
civitas.ingestion.parsers.text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Plain text, Markdown, and HTML parsers.
"""

from __future__ import annotations

import re
import logging

from civitas.ingestion.connectors.base import RawDocumentBlob
from civitas.ingestion.parsers.base import BaseParser, ParseResult

logger = logging.getLogger(__name__)


class TextParser(BaseParser):
    """Plain text and Markdown parser."""

    @property
    def supported_extensions(self) -> list[str]:
        return [".txt", ".text", ".md", ".markdown", ".rst"]

    def parse(self, blob: RawDocumentBlob) -> ParseResult:
        encoding = blob.detected_encoding or "utf-8"
        try:
            text = blob.content_bytes.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = blob.content_bytes.decode("utf-8", errors="replace")

        headings = re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
        return ParseResult(text=text.strip(), section_headings=headings)


class HtmlParser(BaseParser):
    """
    HTML parser using BeautifulSoup.
    Strips navigation, scripts, styles — keeps body content only.
    Converts to clean text using markdownify for structure preservation.
    """

    @property
    def supported_extensions(self) -> list[str]:
        return [".html", ".htm"]

    def parse(self, blob: RawDocumentBlob) -> ParseResult:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise ImportError("beautifulsoup4 required: pip install beautifulsoup4")

        encoding = blob.detected_encoding or "utf-8"
        html_text = blob.content_bytes.decode(encoding, errors="replace")
        soup = BeautifulSoup(html_text, "lxml")

        # Remove boilerplate tags
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Extract title
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # Extract headings
        headings = [
            h.get_text(strip=True)
            for h in soup.find_all(["h1", "h2", "h3", "h4"])
        ]

        # Extract clean text
        body = soup.find("body") or soup
        text = body.get_text(separator="\n", strip=True)

        # Remove excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)

        return ParseResult(
            text=text,
            section_headings=headings,
            extra={"html_title": title},
        )


class ParserRegistry:
    """
    Registry of all available parsers.
    Resolves the correct parser for a given file extension.
    """

    def __init__(self) -> None:
        self._parsers: dict[str, BaseParser] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        from civitas.ingestion.parsers.pdf import PdfParser
        for parser in [PdfParser(), TextParser(), HtmlParser(), DocxParserPlaceholder()]:
            for ext in parser.supported_extensions:
                self._parsers[ext] = parser

    def get(self, extension: str) -> BaseParser | None:
        return self._parsers.get(extension.lower())

    def register(self, parser: BaseParser) -> None:
        for ext in parser.supported_extensions:
            self._parsers[ext] = parser


class DocxParserPlaceholder(BaseParser):
    """Proxy to avoid circular import at registry load time."""
    @property
    def supported_extensions(self) -> list[str]:
        return [".docx", ".doc"]

    def parse(self, blob: RawDocumentBlob) -> ParseResult:
        from civitas.ingestion.parsers.docx import DocxParser
        return DocxParser().parse(blob)

"""
civitas.ingestion.parsers.pdf
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PDF parser using pypdf. Extracts text page-by-page.
Preserves page numbers for chunk-level metadata.
"""

from __future__ import annotations

import io
import logging

from civitas.ingestion.connectors.base import RawDocumentBlob
from civitas.ingestion.parsers.base import BaseParser, ParseResult

logger = logging.getLogger(__name__)


class PdfParser(BaseParser):
    """
    PDF document parser.

    Extracts text from each page using pypdf.
    Page separators are preserved as markers for downstream chunkers.
    Scanned PDFs (image-only) will return empty text with a warning.
    """

    PAGE_SEPARATOR = "\n\n--- PAGE {page} ---\n\n"

    @property
    def supported_extensions(self) -> list[str]:
        return [".pdf"]

    def parse(self, blob: RawDocumentBlob) -> ParseResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ImportError("pypdf is required for PDF parsing: pip install pypdf")

        reader = PdfReader(io.BytesIO(blob.content_bytes))
        pages: list[str] = []
        headings: list[str] = []
        warnings: list[str] = []

        for i, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(self.PAGE_SEPARATOR.format(page=i) + page_text)
                # Heuristic heading detection: short lines at start of page
                lines = page_text.strip().split("\n")
                if lines and len(lines[0]) < 120:
                    headings.append(lines[0].strip())
            else:
                warnings.append(f"Page {i} contains no extractable text (may be scanned image)")

        full_text = "\n".join(pages)
        if not full_text.strip():
            return ParseResult(
                text="",
                page_count=len(reader.pages),
                error="No extractable text found. Document may be a scanned image.",
            )

        return ParseResult(
            text=full_text,
            page_count=len(reader.pages),
            section_headings=headings[:50],
            warnings=warnings,
            extra={"pdf_version": reader.pdf_header},
        )

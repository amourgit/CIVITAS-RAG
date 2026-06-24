"""
civitas.ingestion.transformers.enricher
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Document enricher — adds derived metadata to a Document after parsing.

Enrichment phases:
  1. Language detection    — detect content language
  2. Keyword extraction    — TF-IDF or LLM-based keyword extraction
  3. Auto-classification   — suggest taxonomy path from content
  4. Quality scoring       — compute quality dimensions
  5. Summary generation    — optional LLM-generated abstract
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from civitas.core.models.document import Document, DocumentLanguage
from civitas.core.models.metadata import DocumentQuality

logger = logging.getLogger(__name__)


class LanguageDetector:
    """Lightweight language detector using langdetect."""

    def detect(self, text: str) -> DocumentLanguage:
        if not text or len(text.strip()) < 20:
            return DocumentLanguage.UNKNOWN
        try:
            from langdetect import detect as langdetect_detect
            code = langdetect_detect(text[:2000])
            try:
                return DocumentLanguage(code)
            except ValueError:
                return DocumentLanguage.UNKNOWN
        except Exception:
            return DocumentLanguage.UNKNOWN


class KeywordExtractor:
    """Extract key terms from document content using statistical methods."""

    def extract(self, text: str, max_keywords: int = 20) -> list[str]:
        """
        Simple frequency-based keyword extraction.
        For production, replace with spaCy NER or KeyBERT.
        """
        if not text:
            return []
        # Tokenize and clean
        words = re.findall(r"\b[a-zA-ZÀ-ÿ]{4,}\b", text.lower())
        stopwords = {
            "this", "that", "with", "have", "from", "they", "will", "been",
            "were", "their", "each", "which", "when", "there", "than", "then",
            "also", "into", "over", "your", "more", "such", "what", "some",
        }
        filtered = [w for w in words if w not in stopwords]
        freq: dict[str, int] = {}
        for w in filtered:
            freq[w] = freq.get(w, 0) + 1
        sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        return [w for w, _ in sorted_words[:max_keywords]]


class QualityScorer:
    """Score document quality across multiple dimensions."""

    def __init__(
        self,
        min_word_count: int = 50,
        max_word_count: int = 500_000,
    ) -> None:
        self.min_word_count = min_word_count
        self.max_word_count = max_word_count

    def score(self, document: Document) -> DocumentQuality:
        quality = DocumentQuality()
        if not document.content:
            quality.quality_score = 0.0
            quality.quality_issues.append("No content extracted")
            return quality

        word_count = document.word_count or 0
        issues: list[str] = []

        # Completeness: word count within expected range
        if word_count < self.min_word_count:
            completeness = max(0.0, word_count / self.min_word_count)
            issues.append(f"Very short document ({word_count} words)")
        elif word_count > self.max_word_count:
            completeness = 0.7
            issues.append(f"Very long document ({word_count} words) — may need splitting")
        else:
            completeness = 1.0
        quality.completeness_score = round(completeness, 4)

        # Consistency: check for excessive repetition
        lines = document.content.split("\n")
        unique_lines = set(l.strip() for l in lines if l.strip())
        repetition_ratio = len(unique_lines) / max(len(lines), 1)
        quality.consistency_score = round(min(1.0, repetition_ratio * 1.2), 4)
        if repetition_ratio < 0.5:
            issues.append("High content repetition detected")

        # Freshness: based on document date if available (placeholder)
        quality.freshness_score = 1.0   # Will be computed by lifecycle manager

        # Overall quality score (simple average)
        quality.quality_score = round(
            (quality.completeness_score + quality.consistency_score + quality.freshness_score) / 3,
            4,
        )
        quality.quality_issues = issues
        return quality


class DocumentEnricher:
    """
    Orchestrates all enrichment phases on a Document.

    Usage:
        enricher = DocumentEnricher()
        enriched_doc = enricher.enrich(document)
    """

    def __init__(
        self,
        language_detector: Optional[LanguageDetector] = None,
        keyword_extractor: Optional[KeywordExtractor] = None,
        quality_scorer: Optional[QualityScorer] = None,
    ) -> None:
        self.language_detector = language_detector or LanguageDetector()
        self.keyword_extractor = keyword_extractor or KeywordExtractor()
        self.quality_scorer = quality_scorer or QualityScorer()

    def enrich(self, document: Document) -> Document:
        """Apply all enrichment phases to the document. Returns the modified document."""
        if not document.content:
            logger.warning("Skipping enrichment for document %s — no content.", document.id)
            return document

        # Phase 1: Language detection
        detected_lang = self.language_detector.detect(document.content)
        if document.language == DocumentLanguage.UNKNOWN:
            document.language = detected_lang

        # Phase 2: Keyword extraction
        keywords = self.keyword_extractor.extract(document.content)
        if not document.metadata.keywords:
            document.metadata.keywords = keywords

        # Phase 3: Quality scoring
        quality = self.quality_scorer.score(document)
        document.metadata.quality = quality

        # Phase 4: Word count
        if not document.word_count and document.content:
            document.word_count = len(document.content.split())

        document.touch()
        logger.debug(
            "Enriched document %s: lang=%s, keywords=%d, quality=%.2f",
            document.id, document.language, len(keywords),
            quality.composite_score or 0.0,
        )
        return document

"""
civitas.governance.quality.checker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Quality control pipeline for ingested documents.

The quality checker runs a configurable set of QualityRule objects
against each document and produces a QualityReport with:
  · Per-rule pass/fail status
  · Detailed issue descriptions
  · Aggregate quality score
  · Disposition recommendation (ACCEPT | REVIEW | REJECT)
"""

from __future__ import annotations

import abc
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from civitas.core.models.document import Document

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  CONTRACTS
# ─────────────────────────────────────────────────────────────

class Disposition(str, Enum):
    ACCEPT = "accept"       # Quality is good — auto-approve
    REVIEW = "review"       # Borderline — human review required
    REJECT = "reject"       # Quality too low — reject automatically


@dataclass
class RuleResult:
    rule_name: str
    passed: bool
    score: float            # 0.0 → 1.0
    message: str
    severity: str = "warning"   # info | warning | error


@dataclass
class QualityReport:
    document_id: str
    document_title: str
    rule_results: list[RuleResult] = field(default_factory=list)
    composite_score: float = 0.0
    disposition: Disposition = Disposition.REVIEW
    issues: list[str] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.rule_results if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.rule_results if not r.passed)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.rule_results if not r.passed and r.severity == "error")

    def summary(self) -> str:
        return (
            f"QualityReport[{self.document_id[:8]}]: "
            f"score={self.composite_score:.2f}, "
            f"disposition={self.disposition.value}, "
            f"passed={self.passed_count}/{len(self.rule_results)}"
        )


# ─────────────────────────────────────────────────────────────
#  RULES
# ─────────────────────────────────────────────────────────────

class BaseQualityRule(abc.ABC):
    """Abstract quality rule. Implement check() and return a RuleResult."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Rule identifier."""

    @property
    def severity(self) -> str:
        return "warning"   # Override to 'error' for blocking rules

    @abc.abstractmethod
    def check(self, document: Document) -> RuleResult:
        """Evaluate the rule against the document."""


class MinimumWordCountRule(BaseQualityRule):
    """Documents must have at least N words of content."""

    def __init__(self, min_words: int = 50) -> None:
        self.min_words = min_words

    @property
    def name(self) -> str:
        return "minimum_word_count"

    @property
    def severity(self) -> str:
        return "error"

    def check(self, document: Document) -> RuleResult:
        count = document.word_count or 0
        passed = count >= self.min_words
        score = min(1.0, count / self.min_words) if self.min_words > 0 else 1.0
        return RuleResult(
            rule_name=self.name,
            passed=passed,
            score=round(score, 3),
            message=f"Word count: {count} (minimum: {self.min_words})",
            severity=self.severity,
        )


class NoExcessiveRepetitionRule(BaseQualityRule):
    """Documents with excessive repeated content are flagged."""

    def __init__(self, max_repetition_ratio: float = 0.6) -> None:
        self.max_repetition_ratio = max_repetition_ratio

    @property
    def name(self) -> str:
        return "no_excessive_repetition"

    def check(self, document: Document) -> RuleResult:
        if not document.content:
            return RuleResult(self.name, False, 0.0, "No content to check", self.severity)
        lines = [l.strip() for l in document.content.split("\n") if l.strip()]
        if not lines:
            return RuleResult(self.name, True, 1.0, "No lines to check", self.severity)
        unique = len(set(lines))
        ratio = unique / len(lines)
        passed = ratio >= (1.0 - self.max_repetition_ratio)
        return RuleResult(
            rule_name=self.name,
            passed=passed,
            score=round(min(ratio * 1.5, 1.0), 3),
            message=f"Unique line ratio: {ratio:.1%}",
            severity=self.severity,
        )


class HasRequiredMetadataRule(BaseQualityRule):
    """Documents must have domain, category, and knowledge space set."""

    @property
    def name(self) -> str:
        return "has_required_metadata"

    @property
    def severity(self) -> str:
        return "error"

    def check(self, document: Document) -> RuleResult:
        meta = document.metadata
        missing = []
        if not meta.domain:
            missing.append("domain")
        if not meta.category:
            missing.append("category")
        if not meta.knowledge_space_id:
            missing.append("knowledge_space_id")
        passed = len(missing) == 0
        score = 1.0 - (len(missing) / 3)
        return RuleResult(
            rule_name=self.name,
            passed=passed,
            score=round(score, 3),
            message=(
                "All required metadata present"
                if passed
                else f"Missing required metadata: {', '.join(missing)}"
            ),
            severity=self.severity,
        )


class NoGarbageContentRule(BaseQualityRule):
    """
    Detects documents with garbled or binary content masquerading as text.
    Common with corrupted PDFs or misidentified file types.
    """

    @property
    def name(self) -> str:
        return "no_garbage_content"

    def check(self, document: Document) -> RuleResult:
        if not document.content:
            return RuleResult(self.name, True, 1.0, "No content", self.severity)
        sample = document.content[:2000]
        # Count non-printable characters
        non_printable = sum(1 for c in sample if ord(c) < 32 and c not in "\n\r\t")
        ratio = non_printable / max(len(sample), 1)
        passed = ratio < 0.05   # Less than 5% garbage
        return RuleResult(
            rule_name=self.name,
            passed=passed,
            score=round(max(0.0, 1.0 - ratio * 10), 3),
            message=f"Non-printable character ratio: {ratio:.1%}",
            severity="error" if ratio > 0.15 else "warning",
        )


class ValidTaxonomyPathRule(BaseQualityRule):
    """Document must be classified to a valid taxonomy leaf node."""

    def __init__(self, taxonomy_registry: Optional[object] = None) -> None:
        self.taxonomy_registry = taxonomy_registry

    @property
    def name(self) -> str:
        return "valid_taxonomy_path"

    def check(self, document: Document) -> RuleResult:
        path = document.metadata.taxonomy_path
        if not path:
            return RuleResult(
                self.name, False, 0.0,
                "No taxonomy path assigned",
                "warning",
            )
        if self.taxonomy_registry:
            valid = self.taxonomy_registry.validate_path(path)
            return RuleResult(
                self.name, valid,
                1.0 if valid else 0.5,
                f"Path '{'.'.join(path)}' {'valid' if valid else 'not found in taxonomy'}",
                "warning",
            )
        # No registry — accept any non-empty path
        return RuleResult(self.name, True, 1.0, f"Taxonomy path: {'.'.join(path)}", self.severity)


# ─────────────────────────────────────────────────────────────
#  QUALITY CHECKER
# ─────────────────────────────────────────────────────────────

class QualityChecker:
    """
    Runs all configured quality rules against a Document
    and produces a QualityReport with disposition.

    Thresholds:
      ACCEPT  — composite_score >= accept_threshold AND no error failures
      REJECT  — composite_score < reject_threshold OR any error rule fails
      REVIEW  — everything in between
    """

    def __init__(
        self,
        rules: Optional[list[BaseQualityRule]] = None,
        accept_threshold: float = 0.85,
        reject_threshold: float = 0.35,
    ) -> None:
        self.rules = rules or self._default_rules()
        self.accept_threshold = accept_threshold
        self.reject_threshold = reject_threshold

    def _default_rules(self) -> list[BaseQualityRule]:
        return [
            MinimumWordCountRule(min_words=50),
            NoExcessiveRepetitionRule(),
            HasRequiredMetadataRule(),
            NoGarbageContentRule(),
            ValidTaxonomyPathRule(),
        ]

    def check(self, document: Document) -> QualityReport:
        """Run all rules and return a QualityReport."""
        results: list[RuleResult] = []
        for rule in self.rules:
            try:
                result = rule.check(document)
                results.append(result)
            except Exception as exc:
                logger.warning("Rule '%s' raised an exception: %s", rule.name, exc)
                results.append(RuleResult(
                    rule_name=rule.name, passed=False, score=0.0,
                    message=f"Rule evaluation error: {exc}", severity="warning",
                ))

        # Composite score = weighted average of rule scores
        composite = sum(r.score for r in results) / max(len(results), 1)
        issues = [r.message for r in results if not r.passed]
        has_error_failures = any(r.severity == "error" and not r.passed for r in results)

        if has_error_failures or composite < self.reject_threshold:
            disposition = Disposition.REJECT
        elif composite >= self.accept_threshold and not has_error_failures:
            disposition = Disposition.ACCEPT
        else:
            disposition = Disposition.REVIEW

        report = QualityReport(
            document_id=str(document.id),
            document_title=document.title,
            rule_results=results,
            composite_score=round(composite, 4),
            disposition=disposition,
            issues=issues,
        )
        logger.info(report.summary())
        return report

    def apply_to_document(self, document: Document) -> Document:
        """Run quality check and update document metadata quality fields."""
        report = self.check(document)
        q = document.metadata.quality
        q.quality_score = report.composite_score
        q.quality_issues = report.issues
        from datetime import datetime
        q.quality_checked_at = datetime.utcnow()
        q.quality_checked_by = "system:quality-checker"
        return document

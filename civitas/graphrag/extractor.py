"""
civitas.graphrag.extractor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GraphRAG Entity & Relation Extractor.

Extracts structured knowledge from document chunks:
  · Named entities  (PERSON, ORG, LOCATION, CONCEPT, DATE, …)
  · Typed relations (works_for, located_in, dated, refers_to, …)
  · Entity co-occurrence (appears in same chunk)

Output feeds the GraphBuilder which constructs a networkx graph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class Entity:
    """A named entity extracted from document content."""
    name: str
    entity_type: str        # PERSON | ORG | LOCATION | CONCEPT | DATE | LAW | PRODUCT
    description: Optional[str] = None
    source_chunk_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    aliases: list[str] = field(default_factory=list)


@dataclass
class Relation:
    """A directed typed relation between two entities."""
    source: str             # Entity name
    target: str             # Entity name
    relation_type: str      # works_for | located_in | refers_to | supersedes | ...
    description: Optional[str] = None
    source_chunk_id: Optional[str] = None
    weight: float = 1.0


@dataclass
class ExtractionResult:
    """Output of one extraction run over a set of chunks."""
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    chunk_count: int = 0
    model_used: str = ""
    errors: list[str] = field(default_factory=list)


class EntityRelationExtractor:
    """
    Extracts entities and relations from document chunks using an LLM.

    Two extraction modes:
      · LLM-based (default)    — GPT-4o-mini with structured output
      · spaCy-based (fallback) — fast NER without LLM

    The LLM-based extractor uses a structured prompt that returns
    JSON with entities and relations.
    """

    EXTRACTION_PROMPT = """Extract all named entities and relationships from the following text.

Return ONLY a JSON object with this exact structure:
{
  "entities": [
    {"name": "string", "type": "PERSON|ORG|LOCATION|CONCEPT|DATE|LAW|PRODUCT", "description": "string"}
  ],
  "relations": [
    {"source": "entity_name", "target": "entity_name", "type": "relation_type", "description": "string"}
  ]
}

Common relation types: works_for, located_in, dated, refers_to, supersedes, governs, creates, uses, part_of

Text to analyze:
---
{text}
---

Return only valid JSON, no preamble."""

    def __init__(
        self,
        llm: Optional[Any] = None,
        use_spacy_fallback: bool = True,
        max_chunk_chars: int = 4000,
    ) -> None:
        self.llm = llm
        self.use_spacy_fallback = use_spacy_fallback
        self.max_chunk_chars = max_chunk_chars
        self._spacy_nlp: Optional[Any] = None

    def extract_from_chunks(
        self,
        chunks: list[Any],   # list of DocumentChunk or TextNode
        knowledge_space_name: str = "",
    ) -> ExtractionResult:
        """Extract entities and relations from a list of chunks."""
        all_entities: dict[str, Entity] = {}
        all_relations: list[Relation] = []
        errors: list[str] = []

        for chunk in chunks:
            text = chunk.content if hasattr(chunk, "content") else chunk.get_content()
            chunk_id = str(getattr(chunk, "id", id(chunk)))
            text = text[:self.max_chunk_chars]

            try:
                if self.llm:
                    result = self._extract_with_llm(text, chunk_id)
                elif self.use_spacy_fallback:
                    result = self._extract_with_spacy(text, chunk_id)
                else:
                    continue

                # Merge entities
                for entity in result.get("entities", []):
                    name = entity.get("name", "").strip()
                    if not name:
                        continue
                    if name in all_entities:
                        all_entities[name].source_chunk_ids.append(chunk_id)
                    else:
                        all_entities[name] = Entity(
                            name=name,
                            entity_type=entity.get("type", "CONCEPT"),
                            description=entity.get("description"),
                            source_chunk_ids=[chunk_id],
                        )

                # Collect relations
                for rel in result.get("relations", []):
                    src = rel.get("source", "").strip()
                    tgt = rel.get("target", "").strip()
                    rtype = rel.get("type", "refers_to")
                    if src and tgt:
                        all_relations.append(Relation(
                            source=src,
                            target=tgt,
                            relation_type=rtype,
                            description=rel.get("description"),
                            source_chunk_id=chunk_id,
                        ))
            except Exception as exc:
                errors.append(f"Chunk {chunk_id}: {exc}")
                logger.warning("Extraction error on chunk %s: %s", chunk_id, exc)

        return ExtractionResult(
            entities=list(all_entities.values()),
            relations=all_relations,
            chunk_count=len(chunks),
            model_used="llm" if self.llm else "spacy",
            errors=errors,
        )

    def _extract_with_llm(self, text: str, chunk_id: str) -> dict:
        """Extract using LLM with structured JSON output."""
        import json as _json
        prompt = self.EXTRACTION_PROMPT.format(text=text)
        try:
            response = self.llm.complete(prompt)
            raw = response.text.strip()
            # Clean markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return _json.loads(raw.strip())
        except Exception as exc:
            logger.warning("LLM extraction failed: %s", exc)
            return {"entities": [], "relations": []}

    def _extract_with_spacy(self, text: str, chunk_id: str) -> dict:
        """Fallback: extract entities using spaCy NER (no LLM)."""
        if self._spacy_nlp is None:
            try:
                import spacy
                self._spacy_nlp = spacy.load("en_core_web_sm")
            except Exception:
                logger.warning("spaCy model not available. Install: python -m spacy download en_core_web_sm")
                return {"entities": [], "relations": []}

        doc = self._spacy_nlp(text[:3000])
        entities = [
            {
                "name": ent.text.strip(),
                "type": ent.label_,
                "description": None,
            }
            for ent in doc.ents
            if len(ent.text.strip()) > 1
        ]
        return {"entities": entities, "relations": []}

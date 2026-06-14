"""LLM-based entity and relationship extraction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import NAMESPACE_DNS, uuid5

from app.rag.graph.prompts import EXTRACTION_SYSTEM, EXTRACTION_USER
from app.services.llm import get_llm_service
from app.utils.logger import create_logger

logger = create_logger(__name__)


@dataclass
class ExtractedEntity:
    entity_id: str
    name: str
    type: str
    description: str


@dataclass
class ExtractedRelation:
    source_entity_id: str
    target_entity_id: str
    type: str
    description: str


@dataclass
class ExtractionResult:
    entities: list[ExtractedEntity]
    relationships: list[ExtractedRelation]


class GraphExtractor:
    """Extract entities and relations from a text passage using an LLM."""

    def __init__(self, max_entities: int = 20) -> None:
        self._max_entities = max_entities
        self._llm = get_llm_service()

    @staticmethod
    def entity_id(project_id: str, name: str) -> str:
        normalized = name.strip().lower()
        return str(uuid5(NAMESPACE_DNS, f"{project_id}:entity:{normalized}"))

    async def extract(
        self,
        project_id: str,
        text: str,
    ) -> ExtractionResult:
        response = await self._llm.complete(
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {"role": "user", "content": EXTRACTION_USER.format(text=text[:6000])},
            ],
            temperature=0.0,
            max_tokens=2048,
        )
        payload = _parse_json(response.content)
        entities_raw = payload.get("entities", [])[: self._max_entities]
        relationships_raw = payload.get("relationships", [])

        name_to_id: dict[str, str] = {}
        entities: list[ExtractedEntity] = []
        for item in entities_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            eid = self.entity_id(project_id, name)
            name_to_id[name.lower()] = eid
            entities.append(
                ExtractedEntity(
                    entity_id=eid,
                    name=name,
                    type=str(item.get("type", "Entity")).strip() or "Entity",
                    description=str(item.get("description", "")).strip() or name,
                )
            )

        relationships: list[ExtractedRelation] = []
        for item in relationships_raw:
            if not isinstance(item, dict):
                continue
            source_name = str(item.get("source", "")).strip().lower()
            target_name = str(item.get("target", "")).strip().lower()
            source_id = name_to_id.get(source_name)
            target_id = name_to_id.get(target_name)
            if not source_id or not target_id or source_id == target_id:
                continue
            relationships.append(
                ExtractedRelation(
                    source_entity_id=source_id,
                    target_entity_id=target_id,
                    type=str(item.get("type", "RELATED_TO")).strip() or "RELATED_TO",
                    description=str(item.get("description", "")).strip(),
                )
            )

        return ExtractionResult(entities=entities, relationships=relationships)


def _parse_json(content: str) -> dict:
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM extraction JSON")
        return {}

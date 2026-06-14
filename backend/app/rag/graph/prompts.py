"""Prompt templates for graph entity extraction."""

EXTRACTION_SYSTEM = """You extract structured knowledge from text for a knowledge graph.
Return ONLY valid JSON with this shape:
{
  "entities": [
    {"name": "string", "type": "string", "description": "string"}
  ],
  "relationships": [
    {"source": "entity name", "target": "entity name", "type": "string", "description": "string"}
  ]
}
Use concise entity names. Omit entities or relationships if none are found (empty arrays).
Do not include markdown or explanation outside the JSON."""

EXTRACTION_USER = """Extract entities and relationships from this passage:

---
{text}
---"""

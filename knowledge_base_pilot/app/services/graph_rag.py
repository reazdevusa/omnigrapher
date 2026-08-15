"""GraphRAG service backed by an embedded Kuzu graph database.

This module extracts entities and relationships from ingested document chunks,
stores them in a local Cypher-compatible graph, and provides graph-augmented
retrieval plus global community summaries.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import requests

try:
    import kuzu

    _kuzu_available = True
except Exception:  # pragma: no cover
    _kuzu_available = False

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
GRAPH_RAG_ENABLED = os.getenv("GRAPH_RAG_ENABLED", "true").lower() in {"1", "true", "yes"}
GRAPH_RAG_MODEL = os.getenv("GRAPH_RAG_MODEL", os.getenv("LLM_MODEL", "llama3.1:latest"))
KUZU_DB_PATH = os.getenv("KUZU_DB_PATH", "kuzu_db")

_db = None
_conn = None


def is_available() -> bool:
    return _kuzu_available and GRAPH_RAG_ENABLED


def _get_connection():
    if not _kuzu_available:
        return None
    global _db, _conn
    if _conn is None:
        db_dir = Path(KUZU_DB_PATH)
        db_dir.mkdir(parents=True, exist_ok=True)
        db_file = db_dir / "graph.kuzu"
        _db = kuzu.Database(str(db_file))
        _conn = kuzu.Connection(_db)
        _init_schema(_conn)
    return _conn


def _init_schema(conn):
    """Create node/relation tables if they do not already exist."""
    existing = _table_names(conn)

    if "Entity" not in existing:
        conn.execute(
            "CREATE NODE TABLE Entity(name STRING, type STRING, PRIMARY KEY(name))"
        )
    if "Chunk" not in existing:
        conn.execute(
            "CREATE NODE TABLE Chunk(id STRING, text STRING, source STRING, page INT64, PRIMARY KEY(id))"
        )
    if "Document" not in existing:
        conn.execute(
            "CREATE NODE TABLE Document(id INT64, filename STRING, PRIMARY KEY(id))"
        )

    for rel, ddl in {
        "EXTRACTED_FROM": "CREATE REL TABLE EXTRACTED_FROM(FROM Entity TO Chunk, MANY_MANY)",
        "CONNECTED_TO": "CREATE REL TABLE CONNECTED_TO(FROM Entity TO Entity, relation STRING, MANY_MANY)",
        "IN_DOCUMENT": "CREATE REL TABLE IN_DOCUMENT(FROM Chunk TO Document, MANY_ONE)",
    }.items():
        if rel not in existing:
            try:
                conn.execute(ddl)
            except Exception:
                logger.debug("Relation table %s may already exist", rel)


def _table_names(conn) -> set:
    names = set()
    try:
        result = conn.execute("CALL show_tables() RETURN name, type")
        while result.has_next():
            row = result.get_next()
            names.add(row[0])
    except Exception:
        logger.exception("Unable to list Kuzu tables")
    return names


def _call_ollama(prompt: str, json_mode: bool = False) -> str:
    payload = {
        "model": GRAPH_RAG_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json=payload,
        timeout=5,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()


def _parse_json_response(raw: str) -> Optional[dict]:
    raw = raw.strip()
    if not raw:
        return None
    # Try the whole string first, then look for a fenced JSON block.
    for candidate in [raw, *re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw)]:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _extract_triples(text: str) -> dict:
    """Use a local LLM to extract entity/relationship triples from chunk text."""
    prompt = (
        "Extract named entities and explicit relationships from the text below. "
        "Return only valid JSON with this exact structure:\n"
        '{"entities": [{"name": "...", "type": "..."}], '
        '"relationships": [{"subject": "...", "relation": "...", "object": "..."}]}\n'
        "Allowed entity types: Organization, Product, Concept, Person, Location, Technology, Team, Policy.\n\n"
        f"Text:\n{text[:3000]}\n\nJSON:"
    )
    raw = _call_ollama(prompt, json_mode=True)
    data = _parse_json_response(raw) or {}
    entities = data.get("entities") if isinstance(data.get("entities"), list) else []
    relationships = (
        data.get("relationships") if isinstance(data.get("relationships"), list) else []
    )
    return {
        "entities": [e for e in entities if isinstance(e, dict) and e.get("name")],
        "relationships": [
            r
            for r in relationships
            if isinstance(r, dict) and r.get("subject") and r.get("object")
        ],
    }


def _extract_query_entities(query_text: str) -> list[str]:
    """Pull entity mentions out of a user query for graph anchoring."""
    prompt = (
        "Extract named entities from the user query as a JSON list of short strings. "
        "Return only the JSON list.\n\n"
        f"Query: {query_text[:500]}\n\nJSON list:"
    )
    raw = _call_ollama(prompt, json_mode=True)
    data = _parse_json_response(raw)
    if isinstance(data, list):
        return [str(item).strip() for item in data if item]
    if isinstance(data, dict):
        # Some models wrap the list in an "entities" key.
        items = data.get("entities") or data.get("names") or []
        return [str(item).strip() for item in items if item]
    return []


def _delete_document_graph(conn, document_id: int, chunk_ids: list[str]):
    """Remove a document's chunks and document node; keep shared entities."""
    for chunk_id in chunk_ids:
        try:
            conn.execute(
                "MATCH (:Entity)-[r:EXTRACTED_FROM]->(c:Chunk {id: $id}) DELETE r",
                {"id": chunk_id},
            )
        except Exception:
            pass
        try:
            conn.execute(
                "MATCH (c:Chunk {id: $id}) DELETE c",
                {"id": chunk_id},
            )
        except Exception:
            pass
    try:
        conn.execute(
            "MATCH (d:Document {id: $id}) DELETE d",
            {"id": document_id},
        )
    except Exception:
        pass


def delete_document_by_id(document_id: Optional[int], filename: Optional[str] = None) -> None:
    """Delete all chunks and Document node for a document from the graph.

    This is a best-effort public wrapper; missing graph stores are ignored.
    """
    if not is_available() or document_id is None:
        return
    conn = _get_connection()
    if conn is None:
        return

    # Find chunk ids linked to this document
    chunk_ids: list[str] = []
    try:
        result = conn.execute(
            "MATCH (c:Chunk)-[:IN_DOCUMENT]->(d:Document {id: $id}) RETURN c.id",
            {"id": document_id},
        )
        while result.has_next():
            row = result.get_next()
            chunk_ids.append(str(row[0]))
    except Exception:
        logger.exception("Unable to list chunks for document %s", document_id)

    _delete_document_graph(conn, document_id, chunk_ids)
    logger.info("Graph data deleted for document %s (%s)", document_id, filename)


def ingest_document_graph(
    document_id: int,
    filename: str,
    parent_chunks: list[dict],
) -> dict:
    """Build graph nodes and edges for a freshly ingested document.

    Returns a summary dict with entity/relationship counts.
    """
    conn = _get_connection()
    if conn is None:
        return {"status": "unavailable"}

    chunk_ids = [chunk["parent_id"] for chunk in parent_chunks]
    _delete_document_graph(conn, document_id, chunk_ids)

    conn.execute(
        "CREATE (d:Document {id: $id, filename: $filename})",
        {"id": document_id, "filename": filename},
    )

    entity_count = 0
    relation_count = 0

    for chunk in parent_chunks:
        chunk_id = chunk["parent_id"]
        text = chunk["text"]
        source = chunk.get("source", filename)
        page = int(chunk.get("page", 0))

        conn.execute(
            "CREATE (c:Chunk {id: $id, text: $text, source: $source, page: $page})",
            {"id": chunk_id, "text": text, "source": source, "page": page},
        )
        conn.execute(
            "MATCH (c:Chunk {id: $chunk_id}), (d:Document {id: $doc_id}) "
            "CREATE (c)-[:IN_DOCUMENT]->(d)",
            {"chunk_id": chunk_id, "doc_id": document_id},
        )

        triples = _extract_triples(text)
        for ent in triples["entities"]:
            name = ent["name"].strip()
            etype = ent.get("type", "Concept").strip()
            if not name:
                continue
            conn.execute(
                "MERGE (e:Entity {name: $name}) ON CREATE SET e.type = $type",
                {"name": name, "type": etype},
            )
            conn.execute(
                "MATCH (e:Entity {name: $name}), (c:Chunk {id: $chunk_id}) "
                "CREATE (e)-[:EXTRACTED_FROM]->(c)",
                {"name": name, "chunk_id": chunk_id},
            )
            entity_count += 1

        for rel in triples["relationships"]:
            subject = rel.get("subject", "").strip()
            obj = rel.get("object", "").strip()
            relation = rel.get("relation", "RELATED_TO").strip() or "RELATED_TO"
            if not subject or not obj:
                continue
            conn.execute(
                "MERGE (s:Entity {name: $name}) ON CREATE SET s.type = 'Concept'",
                {"name": subject},
            )
            conn.execute(
                "MERGE (o:Entity {name: $name}) ON CREATE SET o.type = 'Concept'",
                {"name": obj},
            )
            conn.execute(
                "MATCH (s:Entity {name: $subject}), (o:Entity {name: $object}) "
                "CREATE (s)-[:CONNECTED_TO {relation: $relation}]->(o)",
                {"subject": subject, "object": obj, "relation": relation},
            )
            relation_count += 1

    logger.info(
        "Graph ingestion for document %s: %d entities, %d relations",
        filename,
        entity_count,
        relation_count,
    )
    return {
        "status": "indexed",
        "entities": entity_count,
        "relationships": relation_count,
    }


def graph_context(query_text: str, top_k: int = 3) -> list[dict]:
    """Return graph-traversal passages relevant to the query.

    Passages include 1-hop/2-hop entity relationships and the source chunk text
    for matching entities so they can be merged with vector search results.
    """
    conn = _get_connection()
    if conn is None:
        return []

    entities = _extract_query_entities(query_text)
    if not entities:
        return []

    passages: list[dict] = []
    seen = set()

    for name in entities[:top_k]:
        # 1-hop outgoing relationships
        try:
            result = conn.execute(
                "MATCH (e:Entity {name: $name})-[r:CONNECTED_TO]->(other:Entity) "
                "RETURN e.name, r.relation, other.name, other.type LIMIT 10",
                {"name": name},
            )
            while result.has_next():
                row = result.get_next()
                snippet = f"{row[0]} --[{row[1]}]--> {row[2]} ({row[3]})"
                if snippet not in seen:
                    seen.add(snippet)
                    passages.append(
                        {
                            "chunk_id": "graph",
                            "source": "graph",
                            "page": 0,
                            "text": snippet,
                            "score": 0.5,
                        }
                    )
        except Exception:
            logger.exception("1-hop graph query failed for %s", name)

        # 2-hop paths (entity neighborhoods)
        try:
            result = conn.execute(
                "MATCH (e:Entity {name: $name})-[:CONNECTED_TO*1..2]->(other:Entity) "
                "RETURN DISTINCT other.name, other.type LIMIT 10",
                {"name": name},
            )
            while result.has_next():
                row = result.get_next()
                snippet = f"{name} connects to {row[0]} ({row[1]}) through the knowledge graph."
                if snippet not in seen:
                    seen.add(snippet)
                    passages.append(
                        {
                            "chunk_id": "graph",
                            "source": "graph",
                            "page": 0,
                            "text": snippet,
                            "score": 0.5,
                        }
                    )
        except Exception:
            logger.exception("2-hop graph query failed for %s", name)

        # Source chunks that mention this entity
        try:
            result = conn.execute(
                "MATCH (e:Entity {name: $name})<-[:EXTRACTED_FROM]-(c:Chunk) "
                "RETURN c.text, c.source, c.page LIMIT 5",
                {"name": name},
            )
            while result.has_next():
                row = result.get_next()
                text = str(row[0]).strip()
                if text and text not in seen:
                    seen.add(text)
                    passages.append(
                        {
                            "chunk_id": "graph",
                            "source": row[1],
                            "page": int(row[2] or 0),
                            "text": text,
                            "score": 0.5,
                            "graph_entity": name,
                        }
                    )
        except Exception:
            logger.exception("Entity-to-chunk graph query failed for %s", name)

    return passages


def community_summary(query_text: str) -> str:
    """Generate a high-level structural summary across the whole graph.

    Useful for global queries like "What are the main themes across all
    engineering policies?" that vector similarity alone cannot answer.
    """
    conn = _get_connection()
    if conn is None:
        return ""

    try:
        type_result = conn.execute(
            "MATCH (e:Entity) RETURN e.type, count(*) AS cnt ORDER BY cnt DESC LIMIT 10"
        )
        type_lines = []
        while type_result.has_next():
            row = type_result.get_next()
            type_lines.append(f"{row[0]}: {row[1]}")
    except Exception:
        type_lines = []

    try:
        top_result = conn.execute(
            "MATCH (e:Entity)-[:EXTRACTED_FROM]->(c:Chunk) "
            "WITH e, count(c) AS degree "
            "RETURN e.name, e.type, degree ORDER BY degree DESC LIMIT 20"
        )
        top_lines = []
        while top_result.has_next():
            row = top_result.get_next()
            top_lines.append(f"{row[0]} ({row[1]}) appears in {row[2]} chunks")
    except Exception:
        top_lines = []

    if not type_lines and not top_lines:
        return ""

    prompt = (
        "Based on the following knowledge-graph statistics, answer the question in one concise paragraph.\n\n"
        f"Question: {query_text}\n\n"
        "Entity counts by type:\n" + "\n".join(type_lines) + "\n\n"
        "Most connected entities:\n" + "\n".join(top_lines) + "\n\n"
        "Answer:"
    )
    return _call_ollama(prompt)

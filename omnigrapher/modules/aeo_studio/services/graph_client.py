"""Vector / graph store adapters for AEO Studio.

Provides an abstract interface so agents can query the underlying ChromaDB and
Kùzu stores used by OmniGrapher without coupling directly to their internals.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..config import AeoStudioSettings

logger = logging.getLogger(__name__)


class VectorClient(ABC):
    """Abstract vector store client used by Agent 2."""

    @abstractmethod
    def query(self, text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Return relevant chunks with keys: text, source, page, score, chunk_id."""
        ...


class StubVectorClient(VectorClient):
    """Offline stub that returns an empty list; useful for tests and dry runs."""

    def query(self, text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        logger.debug("StubVectorClient.query called for %r", text)
        return []


class ChromaVectorClient(VectorClient):
    """Query OmniGrapher's ChromaDB instance (persistent or HTTP)."""

    def __init__(self, settings: Optional[AeoStudioSettings] = None):
        self.settings = settings or AeoStudioSettings()
        self._client = None
        self._collection = None

    def _get_collection(self):
        if self._collection is not None:
            return self._collection

        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("chromadb is not installed") from exc

        if self.settings.chroma_persistent_path:
            client = chromadb.PersistentClient(path=self.settings.chroma_persistent_path)
        else:
            client = chromadb.HttpClient(host=self.settings.chroma_host, port=self.settings.chroma_port)

        collection_name = self.settings.chroma_collection
        self._collection = client.get_or_create_collection(collection_name)
        return self._collection

    def query(self, text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        collection = self._get_collection()
        try:
            results = collection.query(query_texts=[text], n_results=top_k)
        except Exception:
            logger.exception("ChromaDB query failed for %r", text)
            return []

        chunks = []
        documents = results.get("documents", [[]])[0] or []
        metadatas = results.get("metadatas", [[]])[0] or []
        distances = results.get("distances", [[]])[0] or []
        ids = results.get("ids", [[]])[0] or []

        for doc, meta, distance, chunk_id in zip(documents, metadatas, distances, ids):
            meta = meta or {}
            chunks.append({
                "chunk_id": chunk_id,
                "text": str(doc),
                "source": meta.get("source", meta.get("filename", "unknown")),
                "page": int(meta.get("page", 0)) if str(meta.get("page", "0")).isdigit() else 0,
                "score": 1.0 - float(distance) if distance is not None else 1.0,
            })
        return chunks


class KuzuGraphClient:
    """Thin wrapper around OmniGrapher's embedded Kùzu graph database."""

    def __init__(self, settings: Optional[AeoStudioSettings] = None):
        self.settings = settings or AeoStudioSettings()
        self._db = None
        self._conn = None

    def _connect(self):
        if self._conn is not None:
            return self._conn
        try:
            import kuzu
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("kuzu is not installed") from exc

        db_path = self.settings.kuzu_db_path
        self._db = kuzu.Database(db_path)
        self._conn = kuzu.Connection(self._db)
        return self._conn

    def find_entity(self, name: str) -> Optional[Dict[str, Any]]:
        """Return entity metadata from the graph if present."""
        conn = self._connect()
        try:
            result = conn.execute(
                "MATCH (e:Entity) WHERE e.name = $name RETURN e.name, e.type",
                {"name": name},
            )
            if result.has_next():
                row = result.get_next()
                return {"name": row[0], "type": row[1]}
        except Exception:
            logger.exception("Kùzu entity lookup failed for %r", name)
        return None

    def neighbors(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Return connected entities for a given entity name."""
        conn = self._connect()
        neighbors = []
        try:
            result = conn.execute(
                "MATCH (a:Entity)-[r:CONNECTED_TO]->(b:Entity) WHERE a.name = $name "
                "RETURN b.name, b.type, r.relation LIMIT $limit",
                {"name": name, "limit": limit},
            )
            while result.has_next():
                row = result.get_next()
                neighbors.append({"name": row[0], "type": row[1], "relation": row[2]})
        except Exception:
            logger.exception("Kùzu neighbor query failed for %r", name)
        return neighbors

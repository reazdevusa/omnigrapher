"""Unit tests for document-level RBAC in the retrieval pipeline."""

import os
import unittest
from unittest import mock

import chromadb

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")

from app import rag_engine


def _ephemeral_chroma_client():
    return chromadb.EphemeralClient()


class TestRBACRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._chroma_patch = mock.patch.object(
            rag_engine, "get_chroma_client", _ephemeral_chroma_client
        )
        cls._chroma_patch.start()
        cls._index_patch = mock.patch.object(rag_engine, "_get_index", return_value=None)
        cls._index_patch.start()
        cls._ranker_patch = mock.patch.object(rag_engine, "_get_ranker", return_value=None)
        cls._ranker_patch.start()

    def setUp(self):
        coll = rag_engine.get_chroma_client().get_or_create_collection("knowledge_base")
        try:
            coll.delete(where={"owner_id": {"$gte": 0}})
        except Exception:
            coll.delete(where={})

    @classmethod
    def tearDownClass(cls):
        cls._ranker_patch.stop()
        cls._index_patch.stop()
        cls._chroma_patch.stop()

    def _seed(self, metadatas: list[dict], texts: list[str]):
        collection = rag_engine.get_chroma_client().get_or_create_collection("knowledge_base")
        ids = [f"rbac-{i}" for i in range(len(texts))]
        cleaned = []
        for m in metadatas:
            m = dict(m)
            if not m.get("allowed_roles"):
                m.pop("allowed_roles", None)
            cleaned.append(m)
        collection.add(
            ids=ids,
            documents=texts,
            metadatas=cleaned,
            embeddings=[[0.0] * 10 for _ in texts],
        )

    def test_owner_can_retrieve_private_chunk(self):
        self._seed(
            [{"owner_id": 42, "file_name": "private.pdf", "visibility": "private"}],
            ["The secret project plan covers Q4."],
        )
        results = rag_engine.retrieve_passages(
            "secret project", owner_id=42, source="private.pdf", scope="single"
        )
        self.assertEqual(len(results), 1)
        self.assertIn("secret project", results[0]["text"].lower())

    def test_private_chunk_hidden_from_other_user(self):
        self._seed(
            [{"owner_id": 42, "file_name": "private.pdf", "visibility": "private", "allowed_roles": []}],
            ["The secret project plan covers Q4."],
        )
        results = rag_engine.retrieve_passages(
            "secret project",
            owner_id=99,
            user_id=99,
            user_role="analyst",
            source="private.pdf",
            scope="single",
        )
        self.assertFalse(results)

    def test_allowed_role_can_access_restricted_chunk(self):
        self._seed(
            [
                {
                    "owner_id": 42,
                    "file_name": "restricted.pdf",
                    "visibility": "restricted",
                    "allowed_roles": ["analyst"],
                }
            ],
            ["Financial projections for FY2027."],
        )
        results = rag_engine.retrieve_passages(
            "financial projections",
            owner_id=99,
            user_id=99,
            user_role="analyst",
            source="restricted.pdf",
            scope="single",
        )
        self.assertEqual(len(results), 1)

    def test_unallowed_role_cannot_access_restricted_chunk(self):
        self._seed(
            [
                {
                    "owner_id": 42,
                    "file_name": "restricted.pdf",
                    "visibility": "restricted",
                    "allowed_roles": ["finance"],
                }
            ],
            ["Financial projections for FY2027."],
        )
        results = rag_engine.retrieve_passages(
            "financial projections",
            owner_id=99,
            user_id=99,
            user_role="analyst",
            source="restricted.pdf",
            scope="single",
        )
        self.assertFalse(results)

    def test_public_chunk_visible_to_everyone(self):
        self._seed(
            [
                {
                    "owner_id": 42,
                    "file_name": "public.pdf",
                    "visibility": "public",
                    "allowed_roles": [],
                }
            ],
            ["Company mission statement."],
        )
        results = rag_engine.retrieve_passages(
            "mission statement",
            owner_id=99,
            user_id=99,
            user_role="user",
            source="public.pdf",
            scope="single",
        )
        self.assertEqual(len(results), 1)

    def test_admin_can_access_any_chunk(self):
        self._seed(
            [{"owner_id": 42, "file_name": "admin.pdf", "visibility": "private"}],
            ["Top secret admin notes."],
        )
        results = rag_engine.retrieve_passages(
            "admin notes",
            owner_id=1,
            user_id=1,
            user_role="admin",
            is_admin=True,
            source="admin.pdf",
            scope="single",
        )
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()

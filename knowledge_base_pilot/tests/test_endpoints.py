import os
import shutil
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

# Point tests to isolated files and disable external services.
os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_STORAGE_PATH", "tests/test_kb")

from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.database import Base, CreditBalance, User, create_db_session, sqlite_engine
from app.main import app


class TestEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Wipe previous local storage and recreate the SQLite schema
        if os.path.isdir("tests/test_kb"):
            shutil.rmtree("tests/test_kb")
        Base.metadata.drop_all(bind=sqlite_engine)
        Base.metadata.create_all(bind=sqlite_engine)
        cls.client = TestClient(app)

    def setUp(self):
        db = create_db_session()
        existing = db.query(User).filter(User.username == "testuser").first()
        if existing:
            db.delete(existing)
            db.commit()

        self.user = User(
            username="testuser",
            email="test@example.com",
            hashed_password="x",
            role="admin",
        )
        db.add(self.user)
        db.commit()
        db.refresh(self.user)

        db.add(CreditBalance(user_id=self.user.id, tier="paid", credits=1000.0))
        db.commit()

        user_id = self.user.id
        user_role = self.user.role
        user_api_keys = self.user.api_keys or "{}"
        db.close()

        self.auth_user = SimpleNamespace(
            id=user_id,
            role=user_role,
            api_keys=user_api_keys,
        )
        app.dependency_overrides[get_current_user] = lambda: self.auth_user

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_health(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_available_models(self):
        response = self.client.get("/api/ai/models")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        model_ids = [m["id"] for m in data]
        self.assertIn("gemini-1.5-flash", model_ids)
        self.assertIn("grok-2", model_ids)
        self.assertIn("deepseek-chat", model_ids)
        self.assertGreaterEqual(len(data), 30)
        for m in data:
            self.assertIn("allowed", m)
            self.assertIn("tier", m)

    def test_user_credits(self):
        response = self.client.get("/api/me/credits")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("tier", data)
        self.assertIn("credits", data)

    def test_chat_model_switch_and_credit_deduction(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}, clear=False):
            with mock.patch("app.routers.llm.get_provider") as mock_get_provider:
                fake_provider = mock.MagicMock()
                fake_provider.generate.return_value = mock.MagicMock(
                    text="Hello from the test provider",
                    input_tokens=2,
                    output_tokens=3,
                    model="gemini-1.5-flash",
                    provider="google",
                )
                mock_get_provider.return_value = fake_provider

                response = self.client.post(
                    "/api/ai/chat",
                    json={
                        "model": "gemini-1.5-flash",
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["text"], "Hello from the test provider")
                self.assertIn("remaining_credits", data)
                self.assertLess(data["remaining_credits"], 1000.0)

    def test_no_llm_pure_search(self):
        response = self.client.post(
            "/api/ai/chat",
            json={
                "model": "no_llm",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["model"], "no_llm")
        self.assertEqual(data["cost_usd"], 0.0)
        self.assertEqual(data["remaining_credits"], 1000.0)

    def test_topup_credits(self):
        response = self.client.post(
            "/api/me/credits/topup",
            json={"amount": 5, "mode": "test"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["credits"], 1005.0)
        self.assertEqual(data["mode"], "test")

    def test_byok_unlocks_cloud_model(self):
        # Remove credits and paid tier so permission depends only on BYOK.
        db = create_db_session()
        balance = db.query(CreditBalance).filter_by(user_id=self.auth_user.id).first()
        if balance:
            balance.credits = 0.0
            db.commit()
        db.close()

        self.auth_user.role = "user"
        self.auth_user.api_keys = "{}"
        response = self.client.get("/api/ai/models")
        self.assertEqual(response.status_code, 200)
        gemini = next(m for m in response.json() if m["id"] == "gemini-1.5-flash")
        self.assertFalse(gemini["allowed"])

        self.auth_user.api_keys = '{"google": "fake-google-key-12345"}'
        response = self.client.get("/api/ai/models")
        gemini = next(m for m in response.json() if m["id"] == "gemini-1.5-flash")
        self.assertTrue(gemini["allowed"])

    def test_chat_with_byok_is_free(self):
        self.auth_user.api_keys = '{"google": "fake-google-key-12345"}'
        with mock.patch("app.routers.llm.get_provider") as mock_get_provider:
            fake_provider = mock.MagicMock()
            fake_provider.generate.return_value = mock.MagicMock(
                text="BYOK response",
                input_tokens=2,
                output_tokens=3,
                model="gemini-1.5-flash",
                provider="google",
            )
            mock_get_provider.return_value = fake_provider

            response = self.client.post(
                "/api/ai/chat",
                json={
                    "model": "gemini-1.5-flash",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["text"], "BYOK response")
            self.assertEqual(data["price_usd"], 0.0)
            self.assertEqual(data["remaining_credits"], 1000.0)

    def test_upload_and_raw_document(self):
        with mock.patch("app.main.notify_ingestion_worker"):
            response = self.client.post(
                "/api/upload",
                files=[("files", ("hello.txt", BytesIO(b"hello world"), "text/plain"))],
            )
            self.assertEqual(response.status_code, 202)
            self.assertIn("hello.txt", response.json()["uploaded"])

        raw = self.client.get("/api/documents/hello.txt/raw")
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.text, "hello world")


if __name__ == "__main__":
    unittest.main()

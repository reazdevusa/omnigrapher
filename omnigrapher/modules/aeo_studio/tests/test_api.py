"""FastAPI endpoint tests for AEO Studio Phase 2 API."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigrapher.modules.aeo_studio.config import AeoStudioSettings
from omnigrapher.modules.aeo_studio.database import init_db, create_db_session, get_engine
from omnigrapher.modules.aeo_studio.models import AeoApiKey
from omnigrapher.modules.aeo_studio.router import get_router
from omnigrapher.modules.aeo_studio.services.auth import generate_api_key, hash_api_key


def _make_client(db_url: str):
    from omnigrapher.modules.aeo_studio import config as cfg
    from omnigrapher.modules.aeo_studio import database as dbmod

    cfg._settings = AeoStudioSettings(db_url=db_url)
    dbmod._engine = None
    dbmod._session_factory = None
    init_db()

    app = FastAPI()
    app.include_router(get_router())
    return TestClient(app), get_engine()


def _create_api_key(db, owner_id: int = 42) -> str:
    plain = generate_api_key()
    key = AeoApiKey(
        owner_id=owner_id,
        name="test-key",
        key_hash=hash_api_key(plain),
        key_prefix=plain[:8] + "...",
        scopes=["generate", "publish", "admin"],
        is_active=1,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return plain


def test_generate_endpoint_requires_api_key():
    with tempfile.TemporaryDirectory() as tmp:
        client, engine = _make_client(f"sqlite:///{tmp}/api.db")
        try:
            response = client.post("/api/v1/aeo-studio/generate", json={})
            assert response.status_code == 401
        finally:
            engine.dispose()
        print("[OK] generate_requires_api_key")


def test_generate_single_page():
    with tempfile.TemporaryDirectory() as tmp:
        client, engine = _make_client(f"sqlite:///{tmp}/api.db")
        db = create_db_session()
        try:
            key = _create_api_key(db)
            # Top up credits first.
            client.post(
                "/api/v1/aeo-studio/credits/top-up",
                data={"amount": "10"},
                headers={"X-API-Key": key},
            )
            response = client.post(
                "/api/v1/aeo-studio/generate",
                json={
                    "project": {
                        "name": "Test Roofer",
                        "business_name": "Test Roofer LLC",
                        "business_type": "RoofingContractor",
                        "location": "Seattle, WA",
                        "seed_keywords": ["roof repair"],
                    },
                    "page_types": ["landing", "faq"],
                },
                headers={"X-API-Key": key},
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["status"] == "completed"
            assert data["credits_deducted"] == 2.0
            assert data["page_outputs_created"] == 2
            assert "job_id" in data

            # Check status endpoint.
            status_resp = client.get(
                f"/api/v1/aeo-studio/jobs/{data['job_id']}/status",
                headers={"X-API-Key": key},
            )
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            assert status_data["status"] == "completed"
            assert status_data["progress_percent"] == 100
            assert len(status_data["trace"]) > 0

            # Check result endpoint.
            result_resp = client.get(
                f"/api/v1/aeo-studio/jobs/{data['job_id']}/result",
                headers={"X-API-Key": key},
            )
            assert result_resp.status_code == 200
            result_data = result_resp.json()
            assert len(result_data["pages"]) == 2
            assert result_data["pages"][0]["html"].startswith("<!DOCTYPE html>")
        finally:
            db.close()
            engine.dispose()
        print("[OK] generate_single_page")


def test_generate_insufficient_credits():
    with tempfile.TemporaryDirectory() as tmp:
        client, engine = _make_client(f"sqlite:///{tmp}/api.db")
        db = create_db_session()
        try:
            key = _create_api_key(db)
            response = client.post(
                "/api/v1/aeo-studio/generate",
                json={
                    "project": {
                        "name": "Test Roofer",
                        "business_name": "Test Roofer LLC",
                        "business_type": "RoofingContractor",
                        "location": "Seattle, WA",
                    },
                    "page_types": ["landing"],
                },
                headers={"X-API-Key": key},
            )
            assert response.status_code == 402
        finally:
            db.close()
            engine.dispose()
        print("[OK] generate_insufficient_credits")


def test_csv_bulk_generate():
    with tempfile.TemporaryDirectory() as tmp:
        client, engine = _make_client(f"sqlite:///{tmp}/api.db")
        db = create_db_session()
        try:
            key = _create_api_key(db)
            client.post(
                "/api/v1/aeo-studio/credits/top-up",
                data={"amount": "100"},
                headers={"X-API-Key": key},
            )
            csv_content = (
                "name,business_name,business_type,location,seed_keywords,page_types\n"
                "Site A,Business A LLC,Service,Austin,TX,\"keyword1, keyword2\",\"landing,faq\"\n"
                "Site B,Business B LLC,Service,Dallas,TX,,landing\n"
            )
            response = client.post(
                "/api/v1/aeo-studio/generate/csv",
                files={"file": ("sites.csv", csv_content, "text/csv")},
                headers={"X-API-Key": key},
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["total_jobs"] == 2
            assert data["credits_deducted"] == 3.0  # 2 + 1 pages
        finally:
            db.close()
            engine.dispose()
        print("[OK] csv_bulk_generate")


def test_api_key_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        client, engine = _make_client(f"sqlite:///{tmp}/api.db")
        db = create_db_session()
        try:
            # Bootstrap with an admin key.
            admin_key = _create_api_key(db, owner_id=1)
            response = client.post(
                "/api/v1/aeo-studio/api-keys",
                json={"name": "client-key", "owner_id": 1, "scopes": ["generate"]},
                headers={"X-API-Key": admin_key},
            )
            assert response.status_code == 200
            created = response.json()
            assert created["plain_key"].startswith("aeo_")
            new_key = created["plain_key"]

            # List keys.
            list_resp = client.get(
                "/api/v1/aeo-studio/api-keys",
                headers={"X-API-Key": admin_key},
            )
            assert list_resp.status_code == 200
            assert len(list_resp.json()) == 2

            # Revoke.
            revoke_resp = client.delete(
                f"/api/v1/aeo-studio/api-keys/{created['id']}",
                headers={"X-API-Key": admin_key},
            )
            assert revoke_resp.status_code == 200

            # New key should be rejected.
            fail_resp = client.get(
                "/api/v1/aeo-studio/api-keys",
                headers={"X-API-Key": new_key},
            )
            assert fail_resp.status_code == 401
        finally:
            db.close()
            engine.dispose()
        print("[OK] api_key_lifecycle")


def test_wordpress_publish_missing_page():
    with tempfile.TemporaryDirectory() as tmp:
        client, engine = _make_client(f"sqlite:///{tmp}/api.db")
        db = create_db_session()
        try:
            key = _create_api_key(db, owner_id=42)
            response = client.post(
                "/api/v1/aeo-studio/publish/wordpress",
                json={
                    "page_output_id": 999,
                    "wordpress_url": "https://example.com",
                    "status": "draft",
                },
                headers={"X-API-Key": key},
            )
            assert response.status_code == 404
        finally:
            db.close()
            engine.dispose()
        print("[OK] wordpress_publish_missing_page")


def run_api_tests():
    print("Running AEO Studio Phase 2 API tests...")
    test_generate_endpoint_requires_api_key()
    test_generate_single_page()
    test_generate_insufficient_credits()
    test_csv_bulk_generate()
    test_api_key_lifecycle()
    test_wordpress_publish_missing_page()
    print("All API tests passed.")


if __name__ == "__main__":
    run_api_tests()

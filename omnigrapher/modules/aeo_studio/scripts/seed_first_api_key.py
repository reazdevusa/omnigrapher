"""Bootstrap the first AEO Studio API key directly in the module database.

Use this once to create an initial key before the dashboard or any API client
has credentials. The printed key is shown only once; store it securely.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from omnigrapher.modules.aeo_studio.config import AeoStudioSettings
from omnigrapher.modules.aeo_studio.database import init_db, create_db_session
from omnigrapher.modules.aeo_studio.models import AeoApiKey
from omnigrapher.modules.aeo_studio.services.auth import generate_api_key, hash_api_key


def main():
    settings = AeoStudioSettings()
    print(f"Using database: {settings.db_url}")
    init_db()

    db = create_db_session()
    try:
        existing = db.query(AeoApiKey).filter_by(is_active=1).first()
        if existing:
            print(f"Active API key already exists (id={existing.id}).")
            return

        plain = generate_api_key()
        key = AeoApiKey(
            owner_id=1,
            name="bootstrap-key",
            key_hash=hash_api_key(plain),
            key_prefix=plain[:8] + "...",
            scopes=["*"],
            is_active=1,
        )
        db.add(key)
        db.commit()
        db.refresh(key)
        print(f"Created API key (id={key.id}): {plain}")
        print("Copy this key into the AEO Studio dashboard API key field.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

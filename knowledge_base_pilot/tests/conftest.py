import os
from pathlib import Path

_test_db = Path(__file__).resolve().parent / "test_kb.db"
os.environ["SQLITE_DATABASE_URL"] = f"sqlite:///{_test_db.as_posix()}"
os.environ["USE_POSTGRES"] = "false"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_PATH"] = str(Path(__file__).resolve().parent / "test_kb")

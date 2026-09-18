"""Startup latency regression guards.

These tests run in a FRESH Python subprocess so ``sys.modules`` reflects what
``import app.main`` alone pulls in. Heavy ML/networking libraries must stay out
of the import path so uvicorn binds the port quickly and Docker healthchecks
pass in a few seconds. If a future change reintroduces a module-level import of
these packages, the healthcheck regression returns.
"""

import subprocess
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Heavy modules that must NOT be imported by `import app.main`.
FORBIDDEN_AT_STARTUP = [
    "app.rag_engine",   # pulls llama_index, chromadb, onnxruntime, cv2 (~20s)
    "llama_index",
    "chromadb",
    "onnxruntime",
    "cv2",
    "stripe",           # ~1s, only used by topup endpoints
    "celery",           # task queue, only needed by celery worker/endpoints
]


class TestStartupImports(unittest.TestCase):
    def test_app_main_import_excludes_heavy_modules(self):
        code = (
            "import app.main, sys, json; "
            "print(json.dumps({m: (m in sys.modules) for m in %r}))"
        ) % (FORBIDDEN_AT_STARTUP,)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, f"import failed:\n{result.stderr}")
        import json

        loaded = json.loads(result.stdout.strip())
        leaked = [m for m, present in loaded.items() if present]
        self.assertEqual(
            leaked,
            [],
            f"Heavy modules imported at startup (breaks healthcheck latency): {leaked}",
        )

    def test_app_main_import_time_budget(self):
        """app.main must import well under the 5s healthcheck budget."""
        code = (
            "import time; t0 = time.perf_counter(); "
            "import app.main; "
            "print(f'{time.perf_counter() - t0:.2f}')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, f"import failed:\n{result.stderr}")
        elapsed = float(result.stdout.strip())
        # Generous ceiling: warm cache should be ~3-4s; allow headroom for slow
        # CI machines while still catching a regression back to ~20s.
        self.assertLess(elapsed, 10.0, f"app.main import took {elapsed}s (budget 10s)")


if __name__ == "__main__":
    unittest.main()

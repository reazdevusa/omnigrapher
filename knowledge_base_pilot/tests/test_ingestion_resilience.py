from app.config import _context_limits
from app.ingestion_worker import _classify_error


def test_context_limits_for_six_gb_gpu():
    assert _context_limits(32, 6) == (4096, 2048)


def test_context_limits_scale_with_gpu_memory():
    assert _context_limits(32, 8) == (8192, 4096)
    assert _context_limits(64, 24) == (32768, 8192)


def test_context_limits_for_cpu_only_hosts():
    assert _context_limits(16, None) == (4096, 2048)
    assert _context_limits(32, None) == (8192, 4096)


def test_timeout_error_is_stable():
    code, message = _classify_error("TimeoutError", "processing timeout")
    assert code == "ERR_INGESTION_FAILED"
    assert message


def test_memory_and_unreadable_errors_are_user_friendly():
    assert _classify_error("MemoryError", "")[0] == "ERR_INSUFFICIENT_MEMORY"
    assert _classify_error("ValueError", "No readable text was found")[0] == "ERR_UNREADABLE_DOCUMENT"

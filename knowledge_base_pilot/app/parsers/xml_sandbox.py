"""Sandboxed XML parser with XXE/Billion Laughs protection.

This module uses ``defusedxml`` when available and enforces payload size,
namespace allow-listing, and per-parse CPU/memory limits by running the
parser in a sandboxed child process.
"""

import logging
import multiprocessing as mp
import os
import re
import resource
import signal
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
CPU_LIMIT_SECONDS = 5
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024  # 256 MB

# Allowed XML namespaces for graph import paths.
ALLOWED_NAMESPACES = {
    "http://graphml.graphdrawing.org/graphml",
    "http://schema.org/",
}


def _set_sandbox_limits() -> None:
    """Set tight CPU and memory limits for the current worker process."""
    # Soft CPU time limit; the process will receive SIGXCPU when exceeded.
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS + 1))
    except (OSError, ValueError) as e:
        logger.debug("Could not set CPU limit: %s", e)

    # Virtual memory limit.
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except (OSError, ValueError) as e:
        logger.debug("Could not set memory limit: %s", e)


def _is_namespace_allowed(ns: str) -> bool:
    """Return True if the namespace is in the explicit allowlist."""
    return ns in ALLOWED_NAMESPACES


def _sanitize_namespace_href(href: str) -> bool:
    """Allow a single namespace URI that is in the allowlist.

    The URI is matched after stripping trailing characters.
    """
    return href in ALLOWED_NAMESPACES


class XMLSandboxError(Exception):
    """Raised when an XML payload violates sandbox policy."""


class XMLParseError(Exception):
    """Raised when XML cannot be parsed safely."""


class XMLTimeoutError(Exception):
    """Raised when XML parsing exceeds the CPU time budget."""


def _parse_worker(payload_path: str) -> dict[str, Any]:
    """Worker that runs inside a sandboxed child process.

    It validates the payload size, parses with defusedxml, checks namespaces,
    and returns a summary. It does not return the full tree to avoid leaking
    parsed data back to the parent.
    """
    _set_sandbox_limits()
    signal.signal(signal.SIGALRM, lambda _signum, _frame: (_ for _ in ()).throw(XMLTimeoutError("XML parsing timed out")))
    signal.alarm(CPU_LIMIT_SECONDS)

    try:
        payload = Path(payload_path).read_bytes()
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise XMLSandboxError(f"XML payload exceeds {MAX_PAYLOAD_BYTES} bytes")

        # Check for external entity/DTD references even before parsing.
        text = payload.decode("utf-8", errors="replace")
        if re.search(r"<!ENTITY\s+.*\s+SYSTEM\s+", text, re.IGNORECASE):
            raise XMLSandboxError("External entity declarations are not allowed")
        if re.search(r"<!DOCTYPE\s+", text, re.IGNORECASE):
            raise XMLSandboxError("DTD declarations are not allowed")

        try:
            from defusedxml import ElementTree as DefusedET
        except ImportError:  # pragma: no cover - safety fallback
            import xml.etree.ElementTree as DefusedET  # type: ignore[assignment]

        root = DefusedET.fromstring(payload)

        namespaces: set[str] = set()
        for elem in root.iter():
            tag = elem.tag
            if tag.startswith("{"):
                ns, _ = tag.split("}", 1)
                ns = ns[1:]
                namespaces.add(ns)

        for ns in namespaces:
            if not _is_namespace_allowed(ns):
                raise XMLSandboxError(f"Namespace not allowed: {ns}")

        return {
            "ok": True,
            "root_tag": root.tag,
            "child_count": sum(1 for _ in root.iter()),
        }
    except XMLSandboxError:
        raise
    except XMLTimeoutError:
        raise
    except Exception as exc:
        raise XMLParseError(f"XML parsing failed: {exc}") from exc
    finally:
        signal.alarm(0)


def safe_parse(
    payload: Union[str, bytes],
    timeout: Optional[int] = None,
) -> dict[str, Any]:
    """Parse XML in a sandboxed child process.

    Args:
        payload: XML string or bytes to parse.
        timeout: Max wall-clock seconds to wait for the child.

    Returns:
        A summary dict with ``ok``, ``root_tag`` and ``child_count``.

    Raises:
        XMLSandboxError: on policy violations (size, namespace, XXE).
        XMLParseError: on parse failures.
        XMLTimeoutError: on CPU/wall-clock timeout.
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    if len(payload) > MAX_PAYLOAD_BYTES:
        raise XMLSandboxError(f"XML payload exceeds {MAX_PAYLOAD_BYTES} bytes")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xml") as tmp:
        tmp.write(payload)
        tmp_path = tmp.name

    try:
        ctx = mp.get_context("spawn")
        queue: mp.queues.Queue = ctx.Queue()
        p = ctx.Process(target=_worker_wrapper, args=(tmp_path, queue))
        p.start()
        p.join(timeout or CPU_LIMIT_SECONDS + 2)

        if p.is_alive():
            p.terminate()
            p.join(timeout=2)
            if p.is_alive():
                p.kill()
                p.join(timeout=2)
            raise XMLTimeoutError("XML parsing did not finish within the time budget")

        if queue.empty():
            if p.exitcode and p.exitcode < 0:
                raise XMLTimeoutError("XML sandbox process was terminated by resource limit")
            raise XMLParseError("XML sandbox process returned no result")

        result = queue.get()
        if isinstance(result, Exception):
            raise result
        return result
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _worker_wrapper(payload_path: str, queue: "mp.queues.Queue") -> None:
    """Thin wrapper that catches worker exceptions and puts them on the queue."""
    try:
        result = _parse_worker(payload_path)
        queue.put(result)
    except Exception as exc:
        queue.put(exc)

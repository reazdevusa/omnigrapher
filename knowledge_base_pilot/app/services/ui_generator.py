"""Structured UI output generation.

Wraps raw RAG/CRAG answers into frontend-friendly blocks: tables, key-value
summaries, citation cards, and code snippets.
"""

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _as_number(value: str):
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return None


def _extract_table(text: str) -> Optional[dict[str, Any]]:
    """Parse a Markdown table into a structured table block if found."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "|" not in line:
            continue
        # collect contiguous lines that start/ contain pipes
        table_lines = []
        j = i
        while j < len(lines) and "|" in lines[j]:
            table_lines.append(lines[j])
            j += 1
        if len(table_lines) < 2:
            continue
        rows = [[c.strip() for c in ln.strip().split("|") if c.strip() != ""] for ln in table_lines]
        # Filter out separator row (e.g. --- | ---)
        clean_rows = [r for r in rows if not all(re.match(r"^[-:=\s]+$", c) for c in r)]
        if not clean_rows:
            continue
        headers = clean_rows[0]
        data = clean_rows[1:]
        # pad short rows
        data = [r + [""] * (len(headers) - len(r)) if len(r) < len(headers) else r[: len(headers)] for r in data]
        return {
            "type": "table",
            "title": "Table",
            "headers": headers,
            "rows": data,
            "raw": "\n".join(table_lines),
        }
    return None


def _extract_kvs(text: str) -> list[dict[str, Any]]:
    """Extract `**Key:** value` or `- Key: value` patterns."""
    kvs = []
    pattern = re.compile(r"(?:^|\n)(?:[-*]\s*)?\*\*(.+?)\*\*\s*:?\s*(.+?)(?=\n|$)", re.IGNORECASE)
    for key, value in pattern.findall(text):
        kvs.append({"key": key.strip(), "value": value.strip()})
    # Also try simple "- key: value" bullets
    if not kvs:
        for line in text.splitlines():
            m = re.match(r"(?:[-*]\s*)([^:]+):\s*(.+)", line)
            if m:
                kvs.append({"key": m.group(1).strip(), "value": m.group(2).strip()})
    return kvs


def _extract_code(text: str) -> list[dict[str, Any]]:
    """Find fenced code blocks."""
    blocks = []
    pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    for lang, code in pattern.findall(text):
        blocks.append({"type": "code", "language": lang or "text", "content": code.strip()})
    return blocks


def _build_citations(documents: list[dict]) -> list[dict[str, Any]]:
    """Build citation cards from RAG documents."""
    citations = []
    for idx, doc in enumerate(documents, start=1):
        if not isinstance(doc, dict):
            continue
        citations.append({
            "index": idx,
            "source": doc.get("source") or doc.get("filename") or doc.get("file_name", "unknown"),
            "page": doc.get("page"),
            "text": doc.get("text", "")[:400],
            "score": doc.get("score"),
        })
    return citations


def generate_ui_output(
    query: str,
    text: str,
    documents: Optional[list[dict]] = None,
    triad_scores: Optional[dict] = None,
    intent: Optional[str] = None,
) -> dict[str, Any]:
    """Return a structured UI payload for the frontend."""
    blocks: list[dict[str, Any]] = []

    # Add a summary/text block.
    blocks.append({"type": "text", "content": text, "title": "Answer"})

    # Table block if present and requested.
    if intent in ("table", None):
        table = _extract_table(text)
        if table:
            blocks.append(table)

    # Key/value block.
    kvs = _extract_kvs(text)
    if kvs:
        blocks.append({
            "type": "key_value",
            "title": "Summary",
            "items": kvs,
        })

    # Code blocks.
    for code_block in _extract_code(text):
        blocks.append(code_block)

    citations = _build_citations(documents or [])
    if citations:
        blocks.append({
            "type": "citations",
            "title": "Sources",
            "items": citations,
        })

    result: dict[str, Any] = {
        "format": intent or "text",
        "blocks": blocks,
        "citations": citations,
    }
    if triad_scores:
        result["triad_scores"] = triad_scores

    return result


def ui_output_to_markdown(ui_output: dict[str, Any]) -> str:
    """Serialize a structured UI output into Markdown for legacy consumers."""
    parts = []
    for block in ui_output.get("blocks", []):
        t = block.get("type")
        if t == "text":
            parts.append(block.get("content", ""))
        elif t == "table":
            parts.append("\n" + block.get("raw", ""))
        elif t == "key_value":
            for item in block.get("items", []):
                parts.append(f"- **{item['key']}:** {item['value']}")
        elif t == "code":
            lang = block.get("language", "")
            parts.append(f"```{lang}\n{block.get('content', '')}\n```")
    return "\n\n".join(parts).strip()

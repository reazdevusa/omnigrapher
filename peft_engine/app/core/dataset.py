"""Dataset loading and formatting for PEFT training."""

import json
from pathlib import Path
from typing import List, Literal, Union


def load_jsonl(path: Union[str, Path]) -> List[dict]:
    """Load a JSONL file into a list of records."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def format_chatml(messages: List[dict]) -> str:
    """Convert a list of ChatML messages into a single text string."""
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        parts.append(f"<|{role}|>\n{content}")
    # Add a trailing assistant marker for training completion
    parts.append("<|assistant|>\n")
    return "\n".join(parts)


def format_alpaca(instruction: str, input_text: str = "", output: str = "") -> str:
    """Convert an Alpaca-style record into a single text string."""
    if input_text:
        prompt = (
            "Below is an instruction that describes a task, paired with an input that provides further context.\n\n"
            f"### Instruction:\n{instruction}\n\n"
            f"### Input:\n{input_text}\n\n"
            "### Response:\n"
        )
    else:
        prompt = (
            "Below is an instruction that describes a task.\n\n"
            f"### Instruction:\n{instruction}\n\n"
            "### Response:\n"
        )
    return f"{prompt}{output}"


def build_dataset(
    path: Union[str, Path],
    format: Literal["chatml", "alpaca"] = "chatml",
) -> List[dict]:
    """Load a JSONL file and return records with a formatted `text` field."""
    records = load_jsonl(path)
    formatted = []
    for record in records:
        if format == "chatml":
            messages = record.get("messages")
            if not messages or not isinstance(messages, list):
                raise ValueError(f"ChatML record missing 'messages': {record}")
            text = format_chatml(messages)
        elif format == "alpaca":
            instruction = record.get("instruction", "")
            input_text = record.get("input", "")
            output = record.get("output", "")
            text = format_alpaca(instruction, input_text, output)
        else:
            raise ValueError(f"Unsupported format: {format}")
        formatted.append({"text": text, **record})
    return formatted

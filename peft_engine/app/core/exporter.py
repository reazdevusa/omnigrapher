"""Adapter export utilities: save, merge, and GGUF export."""

import logging
from pathlib import Path
from typing import Optional, Union

from peft_engine.app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def save_adapter(
    model,
    tokenizer,
    output_dir: Union[str, Path],
) -> Path:
    """Save a PEFT LoRA adapter to disk."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    logger.info("Adapter saved to %s", output_dir)
    return output_dir


def merge_adapter_into_base(
    base_model_name: str,
    adapter_path: Union[str, Path],
    output_dir: Union[str, Path],
) -> Path:
    """Merge a LoRA adapter into its base model and save the merged weights."""
    from peft import AutoPeftModelForCausalLM

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Merging adapter %s into base %s", adapter_path, base_model_name)
    model = AutoPeftModelForCausalLM.from_pretrained(adapter_path)
    merged = model.merge_and_unload()
    merged.save_pretrained(output_dir)
    # Copy tokenizer from adapter directory
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(adapter_path)
    tokenizer.save_pretrained(output_dir)
    logger.info("Merged model saved to %s", output_dir)
    return output_dir


def export_gguf(
    merged_model_dir: Union[str, Path],
    output_file: Union[str, Path],
    quantization: Optional[str] = None,
) -> Path:
    """Export a merged model to GGUF format.

    Requires llama.cpp conversion tools. If the tools are not available, this
    raises a clear RuntimeError.
    """
    from transformers import AutoTokenizer

    settings = get_settings()
    quantization = quantization or settings.gguf_quantization
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Exporting GGUF to %s with quantization %s", output_file, quantization)
    try:
        from unsloth import FastLanguageModel

        _, tokenizer = FastLanguageModel.from_pretrained(
            model_name=str(merged_model_dir),
            max_seq_length=settings.max_seq_length,
            dtype=None,
            load_in_4bit=False,
        )
        FastLanguageModel.save_to_gguf(
            model_name=str(merged_model_dir),
            tokenizer=tokenizer,
            quantization_method=quantization,
            output_filename=str(output_file),
        )
        return output_file
    except Exception as exc:
        logger.exception("GGUF export failed")
        raise RuntimeError(
            "GGUF export failed. Ensure llama.cpp/Unsloth export dependencies are installed."
        ) from exc


def export_pipeline(
    adapter_name: str,
    base_model_name: Optional[str] = None,
    quantization: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> dict:
    """Run the full export pipeline: merge adapter -> export GGUF."""
    settings = settings or get_settings()
    base_model_name = base_model_name or settings.base_model
    adapter_path = Path(settings.adapter_dir) / adapter_name
    merged_dir = Path(settings.output_dir) / f"{adapter_name}-merged"
    gguf_file = Path(settings.output_dir) / f"{adapter_name}-{quantization or settings.gguf_quantization}.gguf"

    merge_adapter_into_base(base_model_name, adapter_path, merged_dir)
    export_gguf(merged_dir, gguf_file, quantization)
    return {
        "adapter_name": adapter_name,
        "merged_model_path": str(merged_dir),
        "gguf_path": str(gguf_file),
    }

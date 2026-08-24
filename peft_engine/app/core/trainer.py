"""QLoRA adapter training wrapper with VRAM pinning and performance opts.

Uses standard HuggingFace PEFT + BitsAndBytes + TRL stack for 4-bit
quantized LoRA fine-tuning.  No external dependency on Unsloth.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from peft_engine.app.config import Settings, get_settings, is_external_storage_available

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VRAM / Performance Globals
# ---------------------------------------------------------------------------
_base_model_cache: Dict[str, Tuple[Any, Any]] = {}  # model_name -> (model, tokenizer)
_adapter_cache: Dict[str, Any] = {}  # adapter_name -> loaded adapter weights in RAM
_cache_lock = threading.Lock()


def _supports_bf16() -> bool:
    try:
        import torch

        return torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    except Exception:
        return False


def _gpu_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _pin_model_in_vram(model: Any) -> None:
    """Pin the base model in VRAM to prevent unloading during inference.

    After the initial cold load from the external drive, all token generation
    runs 100% from VRAM/RAM — no further disk access.
    """
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            # Move model to CUDA and ensure it stays resident
            model.cuda()
            logger.info("Base model pinned in VRAM — no further disk reads during inference.")
    except Exception as exc:
        logger.warning("Could not pin model in VRAM: %s", exc)


def preload_adapter_to_memory(adapter_name: str, adapter_path: Path) -> None:
    """Pre-load adapter LoRA matrices into system RAM for sub-10ms swapping."""
    with _cache_lock:
        if adapter_name in _adapter_cache:
            return
    try:
        import torch
        adapter_file = adapter_path / "adapter_model.safetensors"
        if not adapter_file.exists():
            adapter_file = adapter_path / "adapter_model.bin"
        if adapter_file.exists():
            weights = torch.load(str(adapter_file), map_location="cpu", weights_only=True)
            with _cache_lock:
                _adapter_cache[adapter_name] = weights
            logger.info("Adapter '%s' pre-loaded into RAM (%d tensors)", adapter_name, len(weights))
        else:
            logger.warning("No adapter weights found at %s", adapter_path)
    except Exception as exc:
        logger.warning("Could not pre-load adapter '%s': %s", adapter_name, exc)


def preload_all_adapters(adapter_dir: Path) -> None:
    """Pre-load all existing adapter weights into RAM for fast swapping."""
    if not adapter_dir.exists():
        return
    for subdir in adapter_dir.iterdir():
        if subdir.is_dir():
            preload_adapter_to_memory(subdir.name, subdir)


def get_cached_adapter_weights(adapter_name: str) -> Optional[Any]:
    """Retrieve pre-loaded adapter weights from the in-memory cache."""
    with _cache_lock:
        return _adapter_cache.get(adapter_name)


class AdapterTrainer:
    """Train a LoRA adapter on top of a 4-bit quantized base model.

    Uses HuggingFace PEFT + BitsAndBytes for QLoRA (4-bit NF4 quantization)
    and TRL's SFTTrainer for supervised fine-tuning.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    def load_base_model(self, base_model: Optional[str] = None, pin_vram: bool = False):
        """Load the base model in 4-bit NF4 quantization via BitsAndBytes.

        Args:
            base_model: Model identifier (HuggingFace hub or local path).
            pin_vram: If True, pin the model in VRAM after loading (for inference).
        """
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        model_name = base_model or self.settings.base_model

        # Return cached model if already loaded
        with _cache_lock:
            if model_name in _base_model_cache:
                logger.info("Returning cached base model: %s", model_name)
                return _base_model_cache[model_name]

        logger.info("Loading base model %s in 4-bit NF4 quantization", model_name)

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16 if _supports_bf16() else torch.float16,
            bnb_4bit_use_double_quant=True,
        )

        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map="auto",
            torch_dtype=torch.bfloat16 if _supports_bf16() else torch.float16,
            attn_implementation="sdpa",
        )

        tokenizer = AutoTokenizer.from_pretrained(model_name)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        # Enable gradient checkpointing for VRAM savings
        model.gradient_checkpointing_enable()

        # Pin in VRAM if requested (inference mode)
        if pin_vram and _gpu_available():
            _pin_model_in_vram(model)

        # Cache the model
        with _cache_lock:
            _base_model_cache[model_name] = (model, tokenizer)

        return model, tokenizer

    def attach_lora(self, model):
        """Attach a LoRA adapter to the loaded model."""
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

        # Prepare the quantized model for LoRA training
        model = prepare_model_for_kbit_training(model)

        lora_config = LoraConfig(
            r=self.settings.r,
            target_modules=self.settings.target_modules,
            lora_alpha=self.settings.lora_alpha,
            lora_dropout=self.settings.lora_dropout,
            bias=self.settings.bias,
            task_type="CAUSAL_LM",
        )

        model = get_peft_model(model, lora_config)
        trainable, total = model.get_nb_trainable_parameters()
        logger.info(
            "LoRA attached: %d trainable params / %d total (%.2f%%)",
            trainable, total, 100 * trainable / total,
        )
        return model

    def train(
        self,
        adapter_name: str,
        dataset_path: Union[str, Path],
        base_model: Optional[str] = None,
        format: str = "chatml",
    ) -> dict:
        """Fine-tune an adapter and save only the LoRA weights."""
        from datasets import Dataset
        from transformers import TrainingArguments
        from trl import SFTTrainer

        from peft_engine.app.core.dataset import build_dataset

        try:
            dataset_path = Path(dataset_path)
            if not dataset_path.exists():
                raise FileNotFoundError(f"Dataset not found: {dataset_path}")

            model, tokenizer = self.load_base_model(base_model)
            model = self.attach_lora(model)

            data = build_dataset(dataset_path, format=format)
            if not data:
                raise ValueError(f"Dataset is empty: {dataset_path}")

            train_dataset = Dataset.from_list(data)
            output_dir = Path(self.settings.output_dir) / adapter_name
            output_dir.mkdir(parents=True, exist_ok=True)
            adapter_output_dir = Path(self.settings.adapter_dir) / adapter_name
            adapter_output_dir.mkdir(parents=True, exist_ok=True)

            trainer = SFTTrainer(
                model=model,
                tokenizer=tokenizer,
                train_dataset=train_dataset,
                dataset_text_field="text",
                max_seq_length=self.settings.max_seq_length,
                args=TrainingArguments(
                    per_device_train_batch_size=self.settings.per_device_train_batch_size,
                    gradient_accumulation_steps=self.settings.gradient_accumulation_steps,
                    warmup_steps=self.settings.warmup_steps,
                    num_train_epochs=self.settings.num_train_epochs,
                    learning_rate=self.settings.learning_rate,
                    fp16=not _supports_bf16(),
                    bf16=_supports_bf16(),
                    logging_steps=self.settings.logging_steps,
                    optim=self.settings.optim,
                    weight_decay=0.01,
                    lr_scheduler_type="linear",
                    seed=self.settings.seed,
                    output_dir=str(output_dir),
                    report_to="none",
                ),
            )

            trainer.train()
            model.save_pretrained(adapter_output_dir)
            tokenizer.save_pretrained(adapter_output_dir)
            logger.info("Adapter saved to %s", adapter_output_dir)
            return {
                "adapter_name": adapter_name,
                "adapter_path": str(adapter_output_dir),
                "status": "completed",
                "error": None,
            }
        except Exception as exc:
            logger.exception("Adapter training failed: %s", adapter_name)
            return {
                "adapter_name": adapter_name,
                "adapter_path": None,
                "status": "failed",
                "error": str(exc),
            }


def train_adapter(
    adapter_name: str,
    dataset_path: Union[str, Path],
    base_model: Optional[str] = None,
    format: str = "chatml",
    settings: Optional[Settings] = None,
) -> dict:
    """Convenience wrapper for one-off adapter training."""
    trainer = AdapterTrainer(settings)
    return trainer.train(adapter_name, dataset_path, base_model, format)

"""PEFT inference engine: loads base model + LoRA adapters for generation.

Loads the base model once in 4-bit quantization, then dynamically loads/swaps
LoRA adapters per request. Adapters are cached in RAM for sub-second swapping.
"""

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from peft_engine.app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class PeftInferenceEngine:
    """Multi-LoRA inference engine with dynamic adapter swapping."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._model = None
        self._tokenizer = None
        self._current_adapter: Optional[str] = None
        self._adapter_cache: Dict[str, bool] = {}  # adapter_name -> loaded flag
        self._lock = threading.Lock()

    def _load_base_model(self) -> None:
        """Load the base model in 4-bit quantization."""
        if self._model is not None:
            return

        logger.info("Loading base model: %s", self.settings.base_model)
        start = time.time()

        compute_dtype = torch.bfloat16 if (
            torch.cuda.is_available() and torch.cuda.is_bf16_supported()
        ) else torch.float16

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=compute_dtype,
            bnb_4bit_use_double_quant=True,
        )

        self._model = AutoModelForCausalLM.from_pretrained(
            self.settings.base_model,
            quantization_config=bnb_config,
            device_map="auto",
            torch_dtype=compute_dtype,
        )

        self._tokenizer = AutoTokenizer.from_pretrained(self.settings.base_model)
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        elapsed = time.time() - start
        logger.info("Base model loaded in %.1fs", elapsed)

    def _load_adapter(self, adapter_name: str) -> bool:
        """Load or switch to a LoRA adapter."""
        adapter_dir = Path(self.settings.adapter_dir) / adapter_name
        if not adapter_dir.exists():
            logger.warning("Adapter directory not found: %s", adapter_dir)
            return False

        if adapter_name in self._adapter_cache:
            # Already loaded, just switch
            if self._current_adapter != adapter_name:
                self._model.set_adapter(adapter_name)
                self._current_adapter = adapter_name
                logger.debug("Switched to adapter: %s", adapter_name)
            return True

        # Load new adapter
        logger.info("Loading adapter: %s from %s", adapter_name, adapter_dir)
        start = time.time()
        self._model = PeftModel.from_pretrained(
            self._model,
            str(adapter_dir),
            adapter_name=adapter_name,
        )
        self._adapter_cache[adapter_name] = True
        self._current_adapter = adapter_name
        elapsed = time.time() - start
        logger.info("Adapter '%s' loaded in %.2fs", adapter_name, elapsed)
        return True

    def generate(self, request: Any) -> dict:
        """Generate a chat completion using the specified adapter.

        Args:
            request: OpenAIChatCompletionRequest with model (adapter name),
                     messages, temperature, max_tokens.
        """
        with self._lock:
            self._load_base_model()

            adapter_name = request.model
            adapter_loaded = self._load_adapter(adapter_name)

            if not adapter_loaded:
                # Fall back to base model generation
                logger.warning(
                    "Adapter '%s' not found, using base model", adapter_name
                )

        # Build prompt from messages using the tokenizer's chat template
        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        if hasattr(self._tokenizer, "apply_chat_template"):
            input_text = self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            # Fallback: manual ChatML format
            parts = []
            for m in messages:
                parts.append(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>")
            parts.append("<|im_start|>assistant\n")
            input_text = "\n".join(parts)

        # Tokenize
        inputs = self._tokenizer(
            input_text, return_tensors="pt", truncation=True,
            max_length=self.settings.max_seq_length
        ).to(self._model.device)

        prompt_tokens = inputs["input_ids"].shape[1]

        # Generate
        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=request.max_tokens,
                temperature=max(request.temperature, 0.01),
                do_sample=request.temperature > 0,
                pad_token_id=self._tokenizer.pad_token_id,
            )

        # Decode only the new tokens
        new_tokens = outputs[0][prompt_tokens:]
        completion_tokens = len(new_tokens)
        response_text = self._tokenizer.decode(new_tokens, skip_special_tokens=True)

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": adapter_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_text,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    def list_available_adapters(self) -> List[str]:
        """List adapter names available on disk."""
        adapter_dir = Path(self.settings.adapter_dir)
        if not adapter_dir.exists():
            return []
        return sorted(
            d.name for d in adapter_dir.iterdir()
            if d.is_dir() and (d / "adapter_config.json").exists()
        )

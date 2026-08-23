"""Unsloth QLoRA adapter training wrapper."""

import logging
from pathlib import Path
from typing import Optional, Union

from peft_engine.app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _supports_bf16() -> bool:
    try:
        import torch

        return torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    except Exception:
        return False


class AdapterTrainer:
    """Train a LoRA adapter on top of a 4-bit base model using Unsloth."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    def load_base_model(self, base_model: Optional[str] = None):
        """Load the base model in 4-bit."""
        from unsloth import FastLanguageModel

        model_name = base_model or self.settings.base_model
        logger.info("Loading base model %s in 4-bit", model_name)
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=model_name,
            max_seq_length=self.settings.max_seq_length,
            dtype=None,
            load_in_4bit=True,
        )
        return model, tokenizer

    def attach_lora(self, model):
        """Attach a LoRA adapter to the loaded model."""
        from unsloth import FastLanguageModel

        return FastLanguageModel.get_peft_model(
            model,
            r=self.settings.r,
            target_modules=self.settings.target_modules,
            lora_alpha=self.settings.lora_alpha,
            lora_dropout=self.settings.lora_dropout,
            bias=self.settings.bias,
            use_gradient_checkpointing=self.settings.use_gradient_checkpointing,
            random_state=self.settings.seed,
            max_seq_length=self.settings.max_seq_length,
        )

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

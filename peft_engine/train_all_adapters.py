"""Train all LoRA adapters for the OmniGrapher PEFT engine.

Run from the peft_engine directory:
    .venv/Scripts/python.exe train_all_adapters.py
"""

import logging
import sys
import time
from pathlib import Path

# Ensure peft_engine package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from peft_engine.app.config import get_settings, is_external_storage_available
from peft_engine.app.core.trainer import AdapterTrainer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

ADAPTERS_TO_TRAIN = [
    "security",
    "summarizer",
    "reasoner",
    "indexer",
    "orchestrator",
]


def main():
    settings = get_settings()
    logger.info("=== OmniGrapher PEFT Adapter Training ===")
    logger.info("Base model: %s", settings.base_model)
    logger.info("External storage: %s (available: %s)", 
                settings.adapter_dir, is_external_storage_available())
    logger.info("Training %d adapters: %s", len(ADAPTERS_TO_TRAIN), ADAPTERS_TO_TRAIN)
    logger.info("LoRA config: r=%d, alpha=%d, target_modules=%s",
                settings.r, settings.lora_alpha, settings.target_modules)
    logger.info("Training config: epochs=%d, lr=%s, batch=%d, grad_accum=%d",
                settings.num_train_epochs, settings.learning_rate,
                settings.per_device_train_batch_size,
                settings.gradient_accumulation_steps)

    trainer = AdapterTrainer(settings)
    results = []

    for adapter_name in ADAPTERS_TO_TRAIN:
        dataset_path = Path(settings.dataset_dir) / f"{adapter_name}.jsonl"
        if not dataset_path.exists():
            logger.warning("Skipping %s — dataset not found at %s", adapter_name, dataset_path)
            results.append({"adapter_name": adapter_name, "status": "skipped"})
            continue

        logger.info("")
        logger.info("=" * 60)
        logger.info("Training adapter: %s", adapter_name)
        logger.info("Dataset: %s", dataset_path)
        logger.info("=" * 60)

        start = time.time()
        result = trainer.train(adapter_name, dataset_path)
        elapsed = time.time() - start

        result["elapsed_seconds"] = round(elapsed, 1)
        results.append(result)
        logger.info("Result: %s (%.1fs)", result["status"], elapsed)

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("TRAINING SUMMARY")
    logger.info("=" * 60)
    for r in results:
        status_icon = "OK" if r.get("status") == "completed" else "FAIL"
        elapsed = r.get("elapsed_seconds", 0)
        logger.info("  [%s] %s (%.1fs)", status_icon, r["adapter_name"], elapsed)

    failed = [r for r in results if r.get("status") not in ("completed", "skipped")]
    if failed:
        logger.error("%d adapter(s) failed!", len(failed))
        for f in failed:
            logger.error("  %s: %s", f["adapter_name"], f.get("error", "unknown"))
        sys.exit(1)
    else:
        logger.info("All adapters trained successfully!")


if __name__ == "__main__":
    main()

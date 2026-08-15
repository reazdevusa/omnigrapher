"""A/B testing and experimentation harness.

Routes users into deterministic experiment cohorts and records per-variant
performance metrics.  Can be used to compare chunking strategies, embedding
models, prompt templates, or retrieval pipelines.
"""

import hashlib
import logging
import time
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

_METRIC_FIELDS = {"latency_ms", "triad_groundedness", "triad_relevance", "triad_context", "user_feedback", "error"}


def _bucket(user_id: str, experiment: str, bucket_count: int = 100) -> int:
    """Deterministic bucket [0, bucket_count) for a user + experiment pair."""
    digest = hashlib.md5(f"{user_id}:{experiment}".encode("utf-8")).hexdigest()
    return int(digest, 16) % bucket_count


def _assign_variant(buckets: list[dict], bucket_id: int) -> str:
    """Map a bucket number to a variant name based on traffic allocation."""
    cumulative = 0
    for variant in buckets:
        cumulative += variant.get("traffic", 0)
        if bucket_id < cumulative:
            return variant["name"]
    # Fallback to the last defined variant.
    return buckets[-1]["name"]


class Experiment:
    """A single A/B experiment definition."""

    def __init__(
        self,
        name: str,
        variants: list[dict],
        description: str = "",
    ):
        self.name = name
        self.variants = variants
        self.description = description
        total = sum(v.get("traffic", 0) for v in variants)
        if total != 100:
            raise ValueError(f"Variant traffic for '{name}' must sum to 100, got {total}")

    def variant_for(self, user_id: str) -> str:
        bucket_id = _bucket(user_id, self.name)
        return _assign_variant(self.variants, bucket_id)

    def config_for(self, user_id: str) -> dict[str, Any]:
        variant = self.variant_for(user_id)
        for v in self.variants:
            if v["name"] == variant:
                return {"experiment": self.name, "variant": variant, **v.get("config", {})}
        return {"experiment": self.name, "variant": variant}


class ExperimentRegistry:
    """In-memory registry of active experiments."""

    def __init__(self):
        self._experiments: dict[str, Experiment] = {}
        self._metrics: dict[str, dict[str, list[dict]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def register(self, experiment: Experiment) -> None:
        self._experiments[experiment.name] = experiment

    def get(self, name: str) -> Optional[Experiment]:
        return self._experiments.get(name)

    def list_experiments(self) -> list[str]:
        return list(self._experiments.keys())

    def assign(self, user_id: str, experiment_name: str) -> Optional[dict[str, Any]]:
        exp = self._experiments.get(experiment_name)
        if not exp:
            return None
        return exp.config_for(user_id)

    def track(
        self,
        experiment_name: str,
        variant: str,
        metrics: dict[str, Any],
    ) -> None:
        record = {"timestamp": time.time()}
        for field in _METRIC_FIELDS:
            record[field] = metrics.get(field)
        # Add any extra metric fields the caller provides.
        for key, value in metrics.items():
            if key not in record:
                record[key] = value
        self._metrics[experiment_name][variant].append(record)

    def report(self, experiment_name: str) -> Optional[dict[str, Any]]:
        if experiment_name not in self._metrics:
            return None
        results: dict[str, Any] = {}
        for variant, records in self._metrics[experiment_name].items():
            if not records:
                continue
            latencies = [r["latency_ms"] for r in records if r.get("latency_ms") is not None]
            grounded = [r["triad_groundedness"] for r in records if r.get("triad_groundedness") is not None]
            feedback = [r["user_feedback"] for r in records if r.get("user_feedback") is not None]
            results[variant] = {
                "count": len(records),
                "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
                "avg_groundedness": sum(grounded) / len(grounded) if grounded else None,
                "avg_user_feedback": sum(feedback) / len(feedback) if feedback else None,
            }
        return {"experiment": experiment_name, "variants": results}


def _default_experiments() -> list[Experiment]:
    return [
        Experiment(
            name="chunking_strategy",
            description="Compare sentence window vs hierarchical parent-child chunking.",
            variants=[
                {"name": "sentence_window", "traffic": 50, "config": {"window": 3}},
                {"name": "hierarchical", "traffic": 50, "config": {"parent_size": 1024, "child_size": 256}},
            ],
        ),
        Experiment(
            name="prompt_template",
            description="Compare concise vs detailed citation prompts.",
            variants=[
                {"name": "concise", "traffic": 50, "config": {"style": "concise"}},
                {"name": "detailed", "traffic": 50, "config": {"style": "detailed"}},
            ],
        ),
    ]


# Global registry with default experiments.
registry = ExperimentRegistry()
for _exp in _default_experiments():
    registry.register(_exp)


def get_experiment_config(user_id: str, experiment_name: str) -> Optional[dict[str, Any]]:
    return registry.assign(user_id, experiment_name)


def track_experiment_metrics(
    experiment_name: str,
    variant: str,
    metrics: dict[str, Any],
) -> None:
    registry.track(experiment_name, variant, metrics)


def get_experiment_report(experiment_name: str) -> Optional[dict[str, Any]]:
    return registry.report(experiment_name)

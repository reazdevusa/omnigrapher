"""Agent 1 — Intent & Entity Researcher.

Finds high-intent local keywords, related entities, and question forms likely to
trigger LLM/answer-engine responses for a business and location.
"""

import re
from typing import Any, Dict, List

from .base import AeoAgentContext, BaseAgent


class IntentEntityResearcher(BaseAgent):
    """Researches keywords, entities, and question patterns for AEO."""

    name = "intent_entity_researcher"
    description = "Finds high-intent local keywords, entities, and question forms."
    required_input_keys = ["business_name", "business_type", "location", "seed_keywords"]
    optional_input_keys = ["target_audience", "website_url", "competitors"]

    # Local/question patterns that are highly likely to surface in answer engines.
    _INTENT_CUES = [
        "near me", "in {location}", "best {business_type} in {location}",
        "{business_type} {location}", "{business_type} near {location}",
        "top rated {business_type} {location}", "affordable {business_type} {location}",
        "{business_type} reviews {location}", "{business_type} open now {location}",
    ]

    _QUESTION_TEMPLATES = [
        "What is the best {business_type} in {location}?",
        "Where can I find a reliable {business_type} in {location}?",
        "How much does {business_type} cost in {location}?",
        "Who offers {business_type} services in {location}?",
        "What should I know before choosing a {business_type} in {location}?",
        "Why choose {business_name} for {business_type} in {location}?",
        "What services does {business_name} offer in {location}?",
    ]

    def run(self, inputs: Dict[str, Any], context: AeoAgentContext) -> Dict[str, Any]:
        self.validate_inputs(inputs)
        context.log_step(self.name, "started", {"inputs_keys": list(inputs.keys())})

        business_name = inputs["business_name"].strip()
        business_type = inputs["business_type"].strip().lower()
        location = inputs["location"].strip()
        seed_keywords = [k.strip() for k in inputs["seed_keywords"] if k.strip()]
        audience = inputs.get("target_audience", "local customers")

        keywords: List[Dict[str, Any]] = []
        entities: List[Dict[str, Any]] = []

        # Core business entity
        entities.append({
            "name": business_name,
            "type": "Organization",
            "salience": 1.0,
            "description": f"{business_name}, a {business_type} in {location}.",
            "source_chunks": [],
        })
        entities.append({
            "name": location,
            "type": "Place",
            "salience": 0.9,
            "description": f"Primary service area for {business_name}.",
            "source_chunks": [],
        })
        entities.append({
            "name": business_type.title(),
            "type": "Service",
            "salience": 0.85,
            "description": f"Service category offered by {business_name}.",
            "source_chunks": [],
        })

        # Generate keyword variations
        seen: set = set()
        for template in self._INTENT_CUES:
            for seed in ([business_type] + seed_keywords[:5]):
                kw = template.format(
                    business_type=business_type,
                    location=location,
                    business_name=business_name,
                )
                kw = kw.replace("{seed}", seed)
                kw = re.sub(r"\s+", " ", kw).strip().lower()
                if kw and kw not in seen:
                    seen.add(kw)
                    keywords.append({
                        "keyword": kw,
                        "intent_type": self._classify_intent(kw),
                        "question_form": self._to_question_form(kw, business_name, business_type, location),
                        "search_volume_estimate": None,
                        "competition": self._guess_competition(kw),
                        "entities": [business_name, location, business_type],
                        "salience": 0.8 if "near me" in kw or location in kw else 0.6,
                    })

        # Seed-derived keywords
        for seed in seed_keywords:
            kw_local = f"{seed} {location}".lower()
            if kw_local not in seen:
                seen.add(kw_local)
                keywords.append({
                    "keyword": kw_local,
                    "intent_type": "local",
                    "question_form": self._to_question_form(kw_local, business_name, business_type, location),
                    "search_volume_estimate": None,
                    "competition": self._guess_competition(kw_local),
                    "entities": [business_name, location],
                    "salience": 0.75,
                })

        # Product/service entities from seeds
        for seed in seed_keywords:
            entities.append({
                "name": seed.title(),
                "type": "Product",
                "salience": 0.6,
                "description": f"{seed.title()} related offering from {business_name}.",
                "source_chunks": [],
            })

        # Dedupe by name/type for entities
        deduped_entities = []
        seen_entities: set = set()
        for e in entities:
            key = (e["name"].lower(), e["type"].lower())
            if key not in seen_entities:
                seen_entities.add(key)
                deduped_entities.append(e)

        # Top questions
        top_questions = [
            t.format(
                business_type=business_type,
                location=location,
                business_name=business_name,
            )
            for t in self._QUESTION_TEMPLATES
        ]

        # If an LLM client is available, use it to refine/expand keywords.
        if context.llm_client is not None:
            keywords = self._llm_enrich(keywords, inputs, context)

        context.log_step(
            self.name,
            "completed",
            {"keywords": len(keywords), "entities": len(deduped_entities), "questions": len(top_questions)},
        )

        return {
            "keywords": keywords,
            "entities": deduped_entities,
            "top_questions": top_questions,
        }

    def _classify_intent(self, keyword: str) -> str:
        keyword = keyword.lower()
        if any(q in keyword for q in ["how", "what", "why", "who", "when"]):
            return "informational"
        if any(q in keyword for q in ["best", "top rated", "reviews"]):
            return "navigational"
        if any(q in keyword for q in ["price", "cost", "buy", "book", "schedule", "near me", "open now"]):
            return "transactional"
        if any(q in keyword for q in ["in ", "near ", "local"]):
            return "local"
        return "informational"

    def _to_question_form(self, keyword: str, business_name: str, business_type: str, location: str) -> str:
        keyword = keyword.strip()
        if keyword.lower().startswith(("what", "how", "why", "who", "where", "when", "is", "are", "can", "does")):
            return keyword.capitalize() + "?"
        return f"Where can I find {keyword} near {location}?".capitalize()

    def _guess_competition(self, keyword: str) -> str:
        # Simple heuristic: generic + location phrases tend to be medium/high competition.
        if "near me" in keyword or "best" in keyword:
            return "high"
        if "in " in keyword:
            return "medium"
        return "low"

    def _llm_enrich(
        self,
        keywords: List[Dict[str, Any]],
        inputs: Dict[str, Any],
        context: AeoAgentContext,
    ) -> List[Dict[str, Any]]:
        """Optionally call an LLM to add long-tail variants.

        This is a no-op when the LLM client returns empty. The deterministic
        keyword list above is always preserved so the agent is useful offline.
        """
        business_type = inputs["business_type"]
        location = inputs["location"]
        prompt = (
            "You are an SEO keyword researcher. Given the business type and location, "
            "return ONLY a JSON object with key 'keywords' containing up to 10 objects. "
            "Each object must have: keyword (string), intent_type (one of informational, "
            "transactional, navigational, local), question_form (string ending in ?), "
            "competition (low, medium, high).\n\n"
            f"Business type: {business_type}\nLocation: {location}\n\nJSON:"
        )
        try:
            raw = context.llm_client.generate_json(prompt, timeout=self.settings.llm_timeout_seconds)
            if raw and isinstance(raw.get("keywords"), list):
                for item in raw["keywords"]:
                    if isinstance(item, dict) and item.get("keyword"):
                        keywords.append({
                            "keyword": str(item["keyword"]).strip().lower(),
                            "intent_type": str(item.get("intent_type", "informational")).lower(),
                            "question_form": str(item.get("question_form", "")).strip(),
                            "search_volume_estimate": None,
                            "competition": str(item.get("competition", "low")).lower(),
                            "entities": [inputs["business_name"], location, business_type],
                            "salience": 0.55,
                        })
        except Exception as exc:
            context.log_step(self.name, "llm_enrich_skipped", {"error": str(exc)})
        return keywords

"""Agent 4 — Fact-Checker & Auditor.

Scores AEO readiness (0-100) and entity coverage, checks grounding of claims
against source chunks, and emits actionable recommendations.
"""

import re
from typing import Any, Dict, List, Optional, Set

from .base import AeoAgentContext, BaseAgent


class FactCheckerAuditor(BaseAgent):
    """Scores AEO readiness, entity coverage, and factual grounding."""

    name = "fact_checker_auditor"
    description = "Scores AEO readiness (0-100) and entity coverage."
    required_input_keys = ["page_output", "grounded_entities", "json_ld_schemas"]
    optional_input_keys = ["sources", "keywords"]

    def run(self, inputs: Dict[str, Any], context: AeoAgentContext) -> Dict[str, Any]:
        self.validate_inputs(inputs)
        context.log_step(self.name, "started", {"page": inputs["page_output"].get("title")})

        page = inputs["page_output"]
        entities = inputs["grounded_entities"]
        schemas = inputs["json_ld_schemas"]
        sources = inputs.get("sources", []) or page.get("sources", [])
        keywords = inputs.get("keywords", []) or page.get("keywords", [])

        # 1. Direct answer quality (target 40-60 words, entities present).
        da = page.get("direct_answer_block", "")
        da_word_count = len(da.split()) if da else 0
        direct_answer_score = self._score_direct_answer(da, da_word_count, entities, keywords)

        # 2. Entity coverage.
        entity_coverage_score = self._score_entity_coverage(page, entities)

        # 3. Schema completeness.
        schema_score = self._score_schema(schemas)

        # 4. Grounding / fact-check.
        grounded_claims, ungrounded_claims, grounding_issues = self._audit_grounding(
            page, sources, entities
        )

        # 5. General issues.
        issues = grounding_issues + self._audit_structure(page, da_word_count, schemas)

        # 6. Final AEO readiness score.
        aeo_score = self._compute_aeo_score(
            direct_answer_score, entity_coverage_score, schema_score, grounded_claims, ungrounded_claims
        )

        recommendations = self._build_recommendations(
            issues, direct_answer_score, entity_coverage_score, schema_score, page
        )

        context.log_step(
            self.name,
            "completed",
            {
                "aeo_score": aeo_score,
                "entity_coverage_score": entity_coverage_score,
                "direct_answer_score": direct_answer_score,
                "schema_score": schema_score,
            },
        )

        return {
            "aeo_score": aeo_score,
            "entity_coverage_score": entity_coverage_score,
            "direct_answer_score": direct_answer_score,
            "schema_score": schema_score,
            "grounded_claims": grounded_claims,
            "ungrounded_claims": ungrounded_claims,
            "issues": issues,
            "recommendations": recommendations,
        }

    def _score_direct_answer(
        self,
        direct_answer: str,
        word_count: int,
        entities: List[Dict[str, Any]],
        keywords: List[Any],
    ) -> float:
        if not direct_answer:
            return 0.0

        score = 0.0
        # Word count target: 40-60 words is ideal.
        if 40 <= word_count <= 60:
            score += 40.0
        elif 30 <= word_count <= 70:
            score += 30.0
        elif word_count > 0:
            score += 15.0

        # Entity presence.
        entity_names = {e["name"].lower() for e in entities if e.get("name")}
        matched = sum(1 for name in entity_names if name in direct_answer.lower())
        score += min(30.0, (matched / max(len(entity_names), 1)) * 30.0)

        # Keyword presence.
        keyword_words: Set[str] = set()
        for kw in keywords:
            if isinstance(kw, dict):
                kw = kw.get("keyword", "")
            if isinstance(kw, str):
                keyword_words.update(kw.lower().split())
        if keyword_words:
            matched_kw = sum(1 for w in keyword_words if w in direct_answer.lower())
            score += min(30.0, (matched_kw / max(len(keyword_words), 1)) * 30.0)
        else:
            score += 20.0

        return round(score, 2)

    def _score_entity_coverage(self, page: Dict[str, Any], entities: List[Dict[str, Any]]) -> float:
        if not entities:
            return 0.0
        content = " ".join(
            str(v) for v in [page.get("direct_answer_block", ""), page.get("page_content", "")] if v
        ).lower()
        matched = sum(1 for e in entities if e.get("name", "").lower() in content)
        return round((matched / len(entities)) * 100.0, 2)

    def _score_schema(self, schemas: List[Dict[str, Any]]) -> float:
        if not schemas:
            return 0.0
        score = 0.0
        present_types = {s.get("schema_type") for s in schemas}
        for required in ["LocalBusiness", "Service", "FAQPage"]:
            if required in present_types:
                score += 30.0
        # Bonus for valid @context and @type.
        for s in schemas:
            payload = s.get("json_payload", {})
            if payload.get("@context") == "https://schema.org" and payload.get("@type"):
                score += 3.33
        return round(min(100.0, score), 2)

    def _audit_grounding(
        self,
        page: Dict[str, Any],
        sources: List[Dict[str, Any]],
        entities: List[Dict[str, Any]],
    ) -> tuple:
        grounded = 0
        ungrounded = 0
        issues = []

        # Simple claim extraction: sentences that contain entity names.
        sentences = re.split(r"(?<=[.!?])\s+", page.get("page_content", ""))
        entity_names = {e["name"].lower() for e in entities if e.get("name")}
        source_text = " ".join(str(s.get("text", "")).lower() for s in sources)

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if any(name in sentence.lower() for name in entity_names):
                if sources and any(name in source_text for name in entity_names if name in sentence.lower()):
                    grounded += 1
                else:
                    ungrounded += 1
                    issues.append({
                        "category": "grounding",
                        "severity": "warning",
                        "message": f"Claim may lack source grounding: {sentence[:120]}",
                        "recommendation": "Attach a citation from the vector store or add an explicit source.",
                    })

        if not sources:
            issues.append({
                "category": "grounding",
                "severity": "warning",
                "message": "No source chunks were provided for fact-checking.",
                "recommendation": "Run Agent 2 with a connected vector store to gather source chunks.",
            })

        return grounded, ungrounded, issues

    def _audit_structure(
        self,
        page: Dict[str, Any],
        da_word_count: int,
        schemas: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        issues = []
        if da_word_count < 40 or da_word_count > 60:
            issues.append({
                "category": "direct_answer",
                "severity": "warning" if da_word_count > 0 else "critical",
                "message": f"Direct answer block is {da_word_count} words (target 40-60).",
                "recommendation": "Rewrite the direct answer block to land between 40 and 60 words.",
            })
        if not page.get("meta_description"):
            issues.append({
                "category": "meta",
                "severity": "warning",
                "message": "Meta description is missing.",
                "recommendation": "Add a concise meta description (150-160 characters) with primary keywords.",
            })
        if not schemas:
            issues.append({
                "category": "schema",
                "severity": "critical",
                "message": "No JSON-LD schemas were generated.",
                "recommendation": "Run Agent 2 (Knowledge Graph Grounding) to emit LocalBusiness, Service, and FAQPage schemas.",
            })
        if not page.get("page_content"):
            issues.append({
                "category": "content",
                "severity": "critical",
                "message": "Page content is empty.",
                "recommendation": "Re-run Agent 3 with valid grounded entities and keywords.",
            })
        return issues

    def _compute_aeo_score(
        self,
        direct_answer_score: float,
        entity_coverage_score: float,
        schema_score: float,
        grounded_claims: int,
        ungrounded_claims: int,
    ) -> float:
        # Weighted average with a small penalty for ungrounded claims.
        raw = (
            direct_answer_score * 0.35
            + entity_coverage_score * 0.30
            + schema_score * 0.25
        )
        if grounded_claims + ungrounded_claims > 0:
            grounding_ratio = grounded_claims / (grounded_claims + ungrounded_claims)
            raw += grounding_ratio * 10.0
        return round(min(100.0, max(0.0, raw)), 2)

    def _build_recommendations(
        self,
        issues: List[Dict[str, Any]],
        direct_answer_score: float,
        entity_coverage_score: float,
        schema_score: float,
        page: Dict[str, Any],
    ) -> List[str]:
        recs = [issue["recommendation"] for issue in issues]
        if direct_answer_score < 80:
            recs.append("Strengthen the direct answer block by front-loading the core answer and entities.")
        if entity_coverage_score < 80:
            recs.append("Increase entity mentions in headings and body copy to improve answer-engine relevance.")
        if schema_score < 80:
            recs.append("Ensure LocalBusiness, Service, and FAQPage JSON-LD schemas are present and valid.")
        if page.get("slug") and any(c in page["slug"] for c in " &_"):
            recs.append("Clean the URL slug: use hyphens and remove spaces/special characters.")
        return list(dict.fromkeys(recs))  # dedupe while preserving order

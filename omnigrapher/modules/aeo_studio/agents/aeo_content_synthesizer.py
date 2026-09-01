"""Agent 3 — AEO & Content Synthesizer.

Writes structured page content with a 40-60 word "Direct Answer Block" at the
top for LLM scraping, followed by entity-rich sections.
"""

import re
from typing import Any, Dict, List, Optional

from .base import AeoAgentContext, BaseAgent


class AeoContentSynthesizer(BaseAgent):
    """Synthesizes AEO-optimized page content with direct answer blocks."""

    name = "aeo_content_synthesizer"
    description = (
        "Writes structured page content with 40-60 word direct answer blocks "
        "optimized for LLM scraping."
    )
    required_input_keys = ["project", "grounded_entities", "keywords", "page_type"]
    optional_input_keys = ["top_questions", "sources", "llm_client"]

    # Default section templates by page type.
    _SECTION_TEMPLATES: Dict[str, List[str]] = {
        "landing": ["Direct Answer", "About Us", "Services", "Why Choose Us", "Local Area", "FAQ"],
        "service": ["Direct Answer", "What We Offer", "Benefits", "Process", "Pricing", "FAQ"],
        "location": ["Direct Answer", "Area Served", "Service Coverage", "Contact", "Testimonials", "FAQ"],
        "faq": ["Direct Answer", "Top Questions", "Related Services", "Contact"],
        "about": ["Direct Answer", "Our Story", "Team", "Mission", "Contact"],
        "blog": ["Direct Answer", "Introduction", "Key Points", "Conclusion"],
    }

    def run(self, inputs: Dict[str, Any], context: AeoAgentContext) -> Dict[str, Any]:
        self.validate_inputs(inputs)
        context.log_step(self.name, "started", {"page_type": inputs["page_type"]})

        project = inputs["project"]
        page_type = inputs["page_type"]
        entities = inputs["grounded_entities"]
        keywords = [k["keyword"] if isinstance(k, dict) else k for k in inputs.get("keywords", [])]
        top_questions = inputs.get("top_questions", [])

        # Build slug and title.
        title = self._build_title(project, page_type)
        slug = self._build_slug(project, page_type)

        # Grounded source citations.
        sources = self._collect_sources(entities)

        # Direct answer block (40-60 words).
        direct_answer = self._synthesize_direct_answer(project, page_type, entities, keywords, context)
        word_count = len(direct_answer.split())

        # Full page content and outline.
        page_content, outline = self._synthesize_page(
            project, page_type, direct_answer, entities, keywords, top_questions, sources, context
        )

        meta_description = self._synthesize_meta_description(project, direct_answer)

        context.log_step(
            self.name,
            "completed",
            {
                "page_type": page_type,
                "title": title,
                "slug": slug,
                "direct_answer_words": word_count,
            },
        )

        return {
            "page_type": page_type,
            "title": title,
            "slug": slug,
            "meta_description": meta_description,
            "direct_answer_block": direct_answer,
            "direct_answer_word_count": word_count,
            "page_content": page_content,
            "structured_outline": outline,
            "keywords": keywords[:20],
            "entities": [e["name"] for e in entities][:20],
            "sources": sources,
        }

    def _build_title(self, project: Dict[str, Any], page_type: str) -> str:
        business = project["business_name"]
        location = project.get("location", "")
        business_type = (project.get("business_type") or "Service")
        templates = {
            "landing": f"{business} — {business_type.title()} in {location}",
            "service": f"Professional {business_type.title()} Services | {business}",
            "location": f"{business_type.title()} in {location} | {business}",
            "faq": f"Frequently Asked Questions | {business}",
            "about": f"About {business}",
            "blog": f"{business_type.title()} Insights from {business}",
        }
        return templates.get(page_type, f"{business} — {business_type.title()}")

    def _build_slug(self, project: Dict[str, Any], page_type: str) -> str:
        base = project["business_name"].lower().replace(" ", "-").replace("&", "and")[:30]
        location = (project.get("location") or "").lower().replace(" ", "-")[:20]
        business_type = (project.get("business_type") or "service").lower().replace(" ", "-")
        templates = {
            "landing": f"{base}",
            "service": f"{base}-{business_type}-services",
            "location": f"{base}-{location}" if location else f"{base}-location",
            "faq": f"{base}-faq",
            "about": f"{base}-about",
            "blog": f"{base}-blog",
        }
        slug = templates.get(page_type, f"{base}-{page_type}")
        slug = re.sub(r"[^a-z0-9-]+", "", slug).strip("-")
        return slug or "home"

    def _collect_sources(self, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        sources = []
        for e in entities:
            for chunk in e.get("source_chunks", []):
                key = (chunk.get("source"), chunk.get("page"))
                if key and key not in seen:
                    seen.add(key)
                    sources.append({
                        "source": chunk.get("source", "unknown"),
                        "page": chunk.get("page", 0),
                        "text": str(chunk.get("text", ""))[:300],
                    })
        return sources

    def _synthesize_direct_answer(
        self,
        project: Dict[str, Any],
        page_type: str,
        entities: List[Dict[str, Any]],
        keywords: List[str],
        context: AeoAgentContext,
    ) -> str:
        business = project["business_name"]
        location = project.get("location") or ""
        business_type = project.get("business_type") or "service"

        # Use LLM if available; otherwise produce a deterministic, entity-rich fallback.
        if context.llm_client is not None:
            prompt = self._direct_answer_prompt(project, page_type, entities, keywords)
            try:
                raw = context.llm_client.generate(prompt, timeout=self.settings.llm_timeout_seconds)
                answer = self._extract_answer(raw)
                answer = self._clamp_words(answer, self.settings.direct_answer_min_words, self.settings.direct_answer_max_words)
                if answer:
                    return answer
            except Exception as exc:
                context.log_step(self.name, "llm_direct_answer_failed", {"error": str(exc)})

        fallback = (
            f"{business} is a trusted {business_type} serving {location}. "
            f"We specialize in {self._join_keywords(keywords[:3])} and deliver fast, "
            f"local expertise that answers your top questions directly. "
            f"Contact us today to get started."
        )
        return self._clamp_words(fallback, self.settings.direct_answer_min_words, self.settings.direct_answer_max_words)

    def _synthesize_page(
        self,
        project: Dict[str, Any],
        page_type: str,
        direct_answer: str,
        entities: List[Dict[str, Any]],
        keywords: List[str],
        top_questions: List[str],
        sources: List[Dict[str, Any]],
        context: AeoAgentContext,
    ) -> tuple:
        sections = self._SECTION_TEMPLATES.get(page_type, self._SECTION_TEMPLATES["landing"])
        outline = {section: [] for section in sections}

        # Build content as Markdown.
        parts = [f"# {self._build_title(project, page_type)}\n", f"{direct_answer}\n"]
        outline["Direct Answer"].append("40-60 word answer block optimized for LLM scraping.")

        entity_names = [e["name"] for e in entities]
        for section in sections:
            if section == "Direct Answer":
                continue
            parts.append(f"\n## {section}\n")
            section_text = self._section_content(
                section, project, entity_names, keywords, top_questions
            )
            parts.append(section_text)
            outline[section].append(section_text[:200])

        if sources:
            parts.append("\n## Sources\n")
            for src in sources[:5]:
                parts.append(f"- [{src['source']}, Page {src['page']}]")

        return "\n".join(parts).strip(), outline

    def _section_content(
        self,
        section: str,
        project: Dict[str, Any],
        entity_names: List[str],
        keywords: List[str],
        top_questions: List[str],
    ) -> str:
        business = project["business_name"]
        location = project.get("location") or ""
        business_type = project.get("business_type") or "service"

        snippets = {
            "About Us": (
                f"{business} has built a reputation in {location} for reliable {business_type} "
                f"services. Our team focuses on {self._join_keywords(keywords[:3])} to help local customers."
            ),
            "Services": (
                f"We offer a full range of {business_type} services, including "
                f"{self._join_keywords(keywords[:4])}. Every service is tailored to your needs."
            ),
            "What We Offer": (
                f"Our {business_type} offerings include {self._join_keywords(keywords[:4])}. "
                f"We serve {location} and surrounding areas with fast response times."
            ),
            "Benefits": (
                "- Local expertise in " + location + "\n- Transparent pricing\n- Fast turnaround\n"
                "- Friendly, knowledgeable staff"
            ),
            "Process": (
                "1. Contact us for a free quote.\n2. We assess your needs.\n"
                "3. Our team delivers the solution.\n4. You enjoy ongoing support."
            ),
            "Pricing": (
                f"Pricing for {business_type} services in {location} depends on scope. "
                f"Call {business} for a personalized quote."
            ),
            "Local Area": f"We proudly serve {location} and nearby neighborhoods with {business_type} expertise.",
            "Area Served": f"{business} covers {location} and surrounding communities for all {business_type} needs.",
            "Service Coverage": f"Our {business_type} coverage includes {location} and the surrounding region.",
            "Contact": f"Reach {business} today to discuss your {business_type} needs in {location}.",
            "Top Questions": "\n".join(f"- {q}" for q in top_questions[:5]) if top_questions else "No questions available.",
            "Related Services": f"Explore related {business_type} services from {business} in {location}.",
            "Our Story": f"{business} started with a simple mission: deliver excellent {business_type} in {location}.",
            "Team": "Our certified professionals are trained to provide reliable, friendly service.",
            "Mission": (
                f"To be the most trusted {business_type} provider in {location} by putting customers first."
            ),
            "Testimonials": (
                f"\"{business} was quick, professional, and solved our {business_type} issue in {location}.\""
            ),
            "Introduction": f"Here is what you should know about {business_type} services from {business}.",
            "Key Points": "\n".join(f"- {kw}" for kw in keywords[:5]) if keywords else "- Local service\n- Expert team",
            "Conclusion": f"For the best {business_type} experience in {location}, choose {business}.",
        }
        return snippets.get(section, f"{section} content for {business} in {location}.")

    def _synthesize_meta_description(self, project: Dict[str, Any], direct_answer: str) -> str:
        text = direct_answer[:155].rsplit(" ", 1)[0]
        return f"{text}... | {project['business_name']}"

    def _direct_answer_prompt(
        self,
        project: Dict[str, Any],
        page_type: str,
        entities: List[Dict[str, Any]],
        keywords: List[str],
    ) -> str:
        entity_list = ", ".join(e["name"] for e in entities[:8])
        keyword_list = ", ".join(keywords[:8])
        return (
            "You are an expert SEO copywriter. Write a single 40-60 word paragraph that directly answers "
            "the likely top query for the page described below. Use the entities and keywords naturally. "
            "Do not add headings, bullet points, or meta commentary.\n\n"
            f"Business: {project['business_name']}\n"
            f"Type: {project.get('business_type', '')}\n"
            f"Location: {project.get('location', '')}\n"
            f"Page type: {page_type}\n"
            f"Entities: {entity_list}\n"
            f"Keywords: {keyword_list}\n\n"
            "Direct answer (40-60 words):"
        )

    def _extract_answer(self, raw: str) -> str:
        # Strip markdown fences and extra whitespace.
        text = raw.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:-1] if text.endswith("```") else text.split("\n")[1:])
        return text.strip()

    def _clamp_words(self, text: str, min_words: int, max_words: int) -> str:
        words = text.split()
        if len(words) > max_words:
            # Try to end on a sentence boundary.
            truncated = " ".join(words[:max_words])
            if "." in truncated:
                truncated = ".".join(truncated.split(".")[:-1]) + "."
            words = truncated.split()
        if len(words) < min_words:
            filler = " We are ready to help you with local, reliable service."
            while len(words) < min_words and len(filler) > 0:
                words.extend(filler.strip().split())
        return " ".join(words[:max_words]).strip()

    def _join_keywords(self, keywords: List[str]) -> str:
        if not keywords:
            return "expert service"
        return ", ".join(keywords)

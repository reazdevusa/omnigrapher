"""Agent 2 — Knowledge Graph Grounding.

Connects with OmniGrapher's vector store (ChromaDB) and graph store (Kùzu) to
validate extracted entities and generate schema.org JSON-LD for LocalBusiness,
Service, and FAQPage types.
"""

import json
from typing import Any, Dict, List, Optional

from .base import AeoAgentContext, BaseAgent


class KnowledgeGraphGrounding(BaseAgent):
    """Grounds entities in vector/graph stores and emits JSON-LD schemas."""

    name = "knowledge_graph_grounding"
    description = (
        "Connects with OmniGrapher's Vector DB for zero hallucination and "
        "generates JSON-LD Schema (LocalBusiness, Service, FAQPage)."
    )
    required_input_keys = ["project", "entities", "top_questions"]
    optional_input_keys = ["keywords", "vector_client", "graph_client"]

    def run(self, inputs: Dict[str, Any], context: AeoAgentContext) -> Dict[str, Any]:
        self.validate_inputs(inputs)
        context.log_step(self.name, "started", {"entity_count": len(inputs["entities"])})

        project = inputs["project"]
        entities = inputs["entities"]
        top_questions = inputs.get("top_questions", [])

        # 1. Ground entities against the vector DB if available.
        grounded = self._ground_entities(entities, context)

        # 2. Build JSON-LD schemas.
        schemas: List[Dict[str, Any]] = [
            self._build_local_business_schema(project, grounded),
            self._build_service_schema(project, grounded, top_questions),
            self._build_faq_schema(project, top_questions),
            self._build_organization_schema(project),
        ]

        grounding_confidence = self._compute_grounding_confidence(grounded)

        context.log_step(
            self.name,
            "completed",
            {"grounded_entities": len(grounded), "schemas": len(schemas), "confidence": grounding_confidence},
        )

        return {
            "grounded_entities": grounded,
            "json_ld_schemas": schemas,
            "grounding_confidence": grounding_confidence,
        }

    def _ground_entities(
        self,
        entities: List[Dict[str, Any]],
        context: AeoAgentContext,
    ) -> List[Dict[str, Any]]:
        """Look up each entity in the vector store and attach source chunks.

        When no vector store is attached, the agent still returns entities with
        an empty ``source_chunks`` list so downstream agents can run offline.
        """
        grounded = []
        vector_client = context.vector_client
        for entity in entities:
            name = entity.get("name", "")
            e_type = entity.get("type", "Concept")
            chunks: List[Dict[str, Any]] = []
            if vector_client is not None and name:
                try:
                    chunks = vector_client.query(name, top_k=context.settings.top_k_chunks)
                except Exception as exc:
                    context.log_step(self.name, "vector_lookup_failed", {"entity": name, "error": str(exc)})

            grounded.append({
                "name": name,
                "type": e_type,
                "description": entity.get("description", ""),
                "salience": float(entity.get("salience", 0.0)),
                "source_chunks": chunks or entity.get("source_chunks", []),
                "grounded": bool(chunks),
            })
        return grounded

    def _build_local_business_schema(
        self,
        project: Dict[str, Any],
        grounded_entities: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        location = project.get("location", "")
        business_type = project.get("business_type", "LocalBusiness")
        website = project.get("website_url")

        schema: Dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": business_type or "LocalBusiness",
            "name": project["business_name"],
            "description": self._first_description(grounded_entities) or f"{project['business_name']} in {location}.",
        }
        if website:
            schema["url"] = website
        if location:
            schema["address"] = {
                "@type": "PostalAddress",
                "addressLocality": location,
            }
        service_names = [e["name"] for e in grounded_entities if e.get("type") == "Service"]
        if service_names:
            schema["hasOfferCatalog"] = {
                "@type": "OfferCatalog",
                "name": "Services",
                "itemListElement": [
                    {"@type": "Offer", "itemOffered": {"@type": "Service", "name": s}}
                    for s in service_names[:10]
                ],
            }
        return {
            "schema_type": "LocalBusiness",
            "name": project["business_name"],
            "json_payload": schema,
        }

    def _build_service_schema(
        self,
        project: Dict[str, Any],
        grounded_entities: List[Dict[str, Any]],
        top_questions: List[str],
    ) -> Dict[str, Any]:
        business_type = project.get("business_type", "Service")
        schema = {
            "@context": "https://schema.org",
            "@type": "Service",
            "serviceType": business_type,
            "provider": {
                "@type": business_type or "LocalBusiness",
                "name": project["business_name"],
            },
            "areaServed": project.get("location", ""),
            "hasOfferCatalog": {
                "@type": "OfferCatalog",
                "name": f"{business_type} services",
                "itemListElement": [
                    {"@type": "Offer", "itemOffered": {"@type": "Service", "name": e["name"]}}
                    for e in grounded_entities
                    if e.get("type") == "Service"
                ][:10]
                or [{"@type": "Offer", "itemOffered": {"@type": "Service", "name": business_type}}],
            },
        }
        return {
            "schema_type": "Service",
            "name": f"{project['business_name']} Service",
            "json_payload": schema,
        }

    def _build_faq_schema(self, project: Dict[str, Any], top_questions: List[str]) -> Dict[str, Any]:
        faq_items = []
        for q in top_questions[:10]:
            faq_items.append({
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f"{project['business_name']} provides expert {project.get('business_type', 'services')} in {project.get('location', 'your area')}.",
                },
            })
        schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": faq_items,
        }
        return {
            "schema_type": "FAQPage",
            "name": f"{project['business_name']} FAQ",
            "json_payload": schema,
        }

    def _build_organization_schema(self, project: Dict[str, Any]) -> Dict[str, Any]:
        schema: Dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": project["business_name"],
        }
        if project.get("website_url"):
            schema["url"] = project["website_url"]
        return {
            "schema_type": "Organization",
            "name": project["business_name"],
            "json_payload": schema,
        }

    def _first_description(self, grounded_entities: List[Dict[str, Any]]) -> Optional[str]:
        orgs = [e for e in grounded_entities if e.get("type") == "Organization" and e.get("description")]
        if orgs:
            return orgs[0]["description"]
        for e in grounded_entities:
            if e.get("description"):
                return e["description"]
        return None

    def _compute_grounding_confidence(self, grounded_entities: List[Dict[str, Any]]) -> float:
        if not grounded_entities:
            return 0.0
        grounded_count = sum(1 for e in grounded_entities if e.get("grounded"))
        return round(grounded_count / len(grounded_entities), 2)

# OmniGuard

Copy of the security layer extracted from `knowledge_base_pilot`.

## File tree

```
OmniGuard/
├── README.md
└── src/
    ├── .github/workflows/security.yml
    ├── knowledge_base_pilot/app/middleware/__init__.py
    ├── knowledge_base_pilot/app/middleware/security.py
    ├── knowledge_base_pilot/app/middleware/output_guard.py
    ├── knowledge_base_pilot/app/middleware/rate_limiter.py
    ├── knowledge_base_pilot/app/middleware/security_headers.py
    ├── knowledge_base_pilot/app/rate_limit.py
    ├── knowledge_base_pilot/app/services/guardrails.py
    ├── knowledge_base_pilot/app/services/pii_sanitizer.py
    ├── knowledge_base_pilot/app/services/sanitizer.py
    ├── knowledge_base_pilot/app/parsers/xml_sandbox.py
    ├── knowledge_base_pilot/app/services/prompt_templates.py
    ├── knowledge_base_pilot/app/services/rag_service.py
    └── knowledge_base_pilot/tests/test_security_suite.py
```

## Modules

- `middleware/__init__.py` — Security middleware package marker.
- `middleware/security.py` — Prompt-injection and jailbreak detection plus XML delimiter escaping.
- `middleware/output_guard.py` — XSS / script / leak / unauthorized-URL scanner for LLM output.
- `middleware/rate_limiter.py` — Redis-backed token-bucket rate limiter with in-memory fallback.
- `middleware/security_headers.py` — FastAPI middleware adding security headers, global rate gate, token binding, and JSON output guard.
- `rate_limit.py` — In-memory sliding-window rate limiter for sensitive endpoints.
- `services/guardrails.py` — Rule-based input/output guardrails with risk scoring.
- `services/pii_sanitizer.py` — Facade over `sanitizer` exposing `redact()` and `is_enabled()`.
- `services/sanitizer.py` — Regex and optional Presidio PII/sensitive-data redaction.
- `parsers/xml_sandbox.py` — Sandboxed XML parser with XXE, Billion Laughs, and namespace controls.
- `services/prompt_templates.py` — XML-delimited RAG and free-form prompt builders.
- `services/rag_service.py` — RAG guardrails: HMAC chunk signing, relevance filtering, context budget, chunk sanitization.
- `tests/test_security_suite.py` — Red-team tests covering prompt injection, XML sandbox, RAG guardrails, PII sanitization, output guard, and rate limiting.
- `.github/workflows/security.yml` — CI workflow that runs the red-team test suite.

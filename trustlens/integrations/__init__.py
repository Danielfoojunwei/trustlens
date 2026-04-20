"""Third-party integration registry.

Dashboard-editable knobs for swapping/toggling data-plane integrations
without a redeploy. Settings live in-process here; wire a Postgres/Consul
backing store via the ``IntegrationsStore`` protocol for HA deployments.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol


INTEGRATION_KINDS = {
    "oracle.wikidata",          # built-in Wikidata SPARQL oracle
    "oracle.customer_kb",       # tenant KB; always available
    "vector.pinecone",          # NEAR
    "vector.pgvector",          # NEAR
    "vector.qdrant",            # NEAR
    "llm.openai",
    "llm.anthropic",
    "llm.ollama",
    "obs.otel",                 # OpenTelemetry export
    "alerts.webhook",           # generic HTTP POST alert webhook
    "alerts.slack",             # Slack incoming webhook
    "alerts.pagerduty",         # PagerDuty Events v2
    "auth.oidc",                # external identity provider
}


@dataclass
class Integration:
    kind: str
    name: str
    enabled: bool = False
    config: dict = field(default_factory=dict)   # provider-specific
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class IntegrationsStore(Protocol):
    def get(self, kind: str) -> Optional[Integration]: ...
    def all(self) -> list[Integration]: ...
    def put(self, integration: Integration) -> None: ...
    def delete(self, kind: str) -> bool: ...


class InMemoryIntegrationsStore:
    """Dev-mode integrations store."""

    def __init__(self, initial: Optional[list[Integration]] = None) -> None:
        self._by_kind: dict[str, Integration] = {}
        for i in initial or []:
            self.put(i)

    def get(self, kind: str) -> Optional[Integration]:
        return self._by_kind.get(kind)

    def all(self) -> list[Integration]:
        return list(self._by_kind.values())

    def put(self, integration: Integration) -> None:
        integration.updated_at = time.time()
        self._by_kind[integration.kind] = integration

    def delete(self, kind: str) -> bool:
        return self._by_kind.pop(kind, None) is not None


def default_integrations() -> list[Integration]:
    return [
        Integration(kind="oracle.customer_kb", name="Customer KB (lexical)", enabled=True),
        Integration(kind="oracle.wikidata",    name="Wikidata SPARQL",       enabled=False),
        Integration(kind="llm.openai",         name="OpenAI-compatible",     enabled=False),
        Integration(kind="llm.anthropic",      name="Anthropic",             enabled=False),
        Integration(kind="llm.ollama",         name="Ollama (local)",        enabled=False),
        Integration(kind="obs.otel",           name="OpenTelemetry export",  enabled=False),
        Integration(kind="alerts.webhook",     name="Generic webhook",       enabled=False),
        Integration(kind="alerts.slack",       name="Slack incoming webhook", enabled=False),
    ]

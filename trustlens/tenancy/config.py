"""Per-tenant configuration.

Every request is bound to a tenant. Tenants differ in:
    - tier (dev / pro / enterprise / deep_inspector)
    - oracles they're allowed to use
    - verification thresholds (tau/tau_prime)
    - rate limits & budgets
    - model backends they're allowed to call
    - whether Deep Inspector features (SSH, RAD-CoT, chain) are enabled
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol


class TenantTier(str, Enum):
    DEV = "dev"                       # development tier, low RPS, Wikidata-only
    PRO = "pro"                       # production tier, KB + Wikidata
    ENTERPRISE = "enterprise"         # high-volume, custom oracles, SLA
    DEEP_INSPECTOR = "deep_inspector" # on-prem / VPC with SSH + RAD-CoT + chains


@dataclass
class TenantConfig:
    """All per-tenant knobs."""
    tenant_id: str
    tier: TenantTier = TenantTier.DEV

    # Oracle allowlist (empty = all enabled)
    allowed_oracles: list[str] = field(default_factory=list)

    # Verification thresholds
    tau: float = 0.6
    tau_prime: float = 0.3

    # Latency budget the gateway will give the verifier
    verify_deadline_ms: int = 500

    # Rate limits
    max_rps: int = 10
    max_tokens_per_minute: int = 100_000

    # Deep Inspector SKU feature flags (default OFF)
    deep_inspector_enabled: bool = False
    ssh_enabled: bool = False
    rad_cot_enabled: bool = False
    agentic_chain_enabled: bool = False

    # Shadow eval opt-in (default ON; customers can disable for sensitive data)
    shadow_eval_opt_in: bool = True

    # Custom signer key id (if customer requires their own root of trust)
    signer_key_id: Optional[str] = None

    # Model backends this tenant may proxy to
    allowed_backends: list[str] = field(
        default_factory=lambda: ["openai", "anthropic", "vllm"]
    )

    def effective_oracles(self, all_oracles: list[str]) -> list[str]:
        if not self.allowed_oracles:
            return all_oracles
        return [o for o in self.allowed_oracles if o in all_oracles]


class TenantConfigStore(Protocol):
    """Where we look up tenant configs at request time."""

    def get(self, tenant_id: str) -> Optional[TenantConfig]: ...


class InMemoryTenantStore:
    """Trivial in-process store. Swap for a DB/KV in production."""

    def __init__(self, configs: Optional[list[TenantConfig]] = None):
        self._configs: dict[str, TenantConfig] = {}
        for c in configs or []:
            self._configs[c.tenant_id] = c

    def put(self, config: TenantConfig) -> None:
        self._configs[config.tenant_id] = config

    def get(self, tenant_id: str) -> Optional[TenantConfig]:
        return self._configs.get(tenant_id)

    def all(self) -> list[TenantConfig]:
        return list(self._configs.values())

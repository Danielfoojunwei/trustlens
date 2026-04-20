"""Tenancy + budget tests."""

from __future__ import annotations

import time

import pytest

from trustlens.tenancy.budget import BudgetExceeded, BudgetTracker
from trustlens.tenancy.config import InMemoryTenantStore, TenantConfig, TenantTier


def test_tenant_store_put_get() -> None:
    store = InMemoryTenantStore()
    store.put(TenantConfig(tenant_id="acme", tier=TenantTier.PRO, max_rps=25))
    loaded = store.get("acme")
    assert loaded is not None
    assert loaded.tier == TenantTier.PRO
    assert loaded.max_rps == 25


def test_effective_oracles_allowlist() -> None:
    c = TenantConfig(tenant_id="x", allowed_oracles=["customer_kb"])
    assert c.effective_oracles(["wikidata", "customer_kb"]) == ["customer_kb"]
    c2 = TenantConfig(tenant_id="x")  # empty allowlist = all
    assert set(c2.effective_oracles(["wikidata", "customer_kb"])) == {
        "wikidata", "customer_kb"
    }


def test_budget_rps_bucket() -> None:
    cfg = TenantConfig(tenant_id="t1", max_rps=2, max_tokens_per_minute=1000)
    bt = BudgetTracker()
    bt.request("t1", cfg)
    bt.request("t1", cfg)
    with pytest.raises(BudgetExceeded) as exc_info:
        bt.request("t1", cfg)
    assert exc_info.value.kind == "rps"


def test_budget_tokens_per_minute() -> None:
    cfg = TenantConfig(tenant_id="t1", max_rps=1000, max_tokens_per_minute=100)
    bt = BudgetTracker()
    # Burn 80 tokens
    bt.request("t1", cfg, tokens_estimate=80)
    bt.record_tokens_used("t1", 80)
    # Next request for 30 more is over budget
    with pytest.raises(BudgetExceeded) as exc_info:
        bt.request("t1", cfg, tokens_estimate=30)
    assert exc_info.value.kind == "tokens_per_minute"

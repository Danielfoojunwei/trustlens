"""End-to-end gateway tests — in-process HTTP via FastAPI TestClient.

Uses the echo backend + in-memory KB oracle so no network dependencies.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from trustlens.certificate.signer import KeyPair
from trustlens.certificate.store import FilesystemStore
from trustlens.gateway.app import build_gateway
from trustlens.gateway.backends import BackendRegistry, EchoBackend
from trustlens.oracles.customer_kb import CustomerKBOracle, KBDocument, LexicalKBIndex
from trustlens.oracles.registry import OracleRegistry
from trustlens.tenancy.config import InMemoryTenantStore, TenantConfig, TenantTier
from trustlens.verifier.engine import VerifierEngine


@pytest.fixture()
def client(tmp_path) -> TestClient:
    kp = KeyPair.generate()
    kb = LexicalKBIndex()
    kb.add_many([
        KBDocument(
            doc_id="d1",
            text="Paris is the capital and most populous city of France.",
            source_uri="kb://fr/paris",
        ),
    ], tenant_id="demo")
    registry = OracleRegistry([CustomerKBOracle(kb)])
    engine = VerifierEngine(registry)
    store = FilesystemStore(tmp_path / "certs")
    tenants = InMemoryTenantStore([
        TenantConfig(
            tenant_id="demo", tier=TenantTier.PRO,
            tau=0.3, tau_prime=0.05,
            max_rps=1000, max_tokens_per_minute=1_000_000,
            allowed_backends=["echo"],
        ),
    ])
    backends = BackendRegistry([EchoBackend()])
    app = build_gateway(
        engine=engine, signer=kp, cert_store=store,
        backend_registry=backends, tenant_store=tenants,
    )
    return TestClient(app)


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz(client: TestClient) -> None:
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert "backends" in body and "oracles" in body


def test_chat_completion_emits_certificate(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers={"X-TrustLens-Tenant-Id": "demo"},
        json={
            "model": "echo",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"]
    assert body["trustlens"]["certificate_id"]
    assert body["trustlens"]["certificate_status"] in (
        "verified", "partial", "blocked", "degraded"
    )
    assert r.headers.get("X-TrustLens-Certificate-Id") == body["trustlens"]["certificate_id"]


def test_rejects_unknown_tenant(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers={"X-TrustLens-Tenant-Id": "nobody"},
        json={
            "model": "echo",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 401


def test_deep_inspector_requires_tier(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers={"X-TrustLens-Tenant-Id": "demo"},
        json={
            "model": "echo",
            "messages": [{"role": "user", "content": "hi"}],
            "trustlens": {"deep_inspector": True},
        },
    )
    # demo tenant is PRO tier, not DEEP_INSPECTOR → must be rejected
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "deep_inspector_unavailable"

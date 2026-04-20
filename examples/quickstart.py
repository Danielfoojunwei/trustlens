"""End-to-end quickstart: start a gateway, issue a chat completion, verify the cert.

Run with:
    python examples/quickstart.py

No external services required — uses the echo backend and an in-memory
knowledge base.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from trustlens.certificate.signer import KeyPair, verify_certificate
from trustlens.certificate.schema import Certificate
from trustlens.certificate.store import FilesystemStore
from trustlens.gateway.app import build_gateway
from trustlens.gateway.backends import BackendRegistry, EchoBackend
from trustlens.oracles.customer_kb import CustomerKBOracle, KBDocument, LexicalKBIndex
from trustlens.oracles.registry import OracleRegistry
from trustlens.tenancy.config import InMemoryTenantStore, TenantConfig, TenantTier
from trustlens.verifier.engine import VerifierEngine


def main() -> None:
    kp = KeyPair.generate()
    kb = LexicalKBIndex()
    kb.add_many([
        KBDocument(
            doc_id="d1",
            text="Paris is the capital of France.",
            source_uri="kb://paris",
        ),
    ], tenant_id="demo")
    engine = VerifierEngine(OracleRegistry([CustomerKBOracle(kb)]))
    tenants = InMemoryTenantStore([
        TenantConfig(
            tenant_id="demo", tier=TenantTier.PRO,
            tau=0.3, tau_prime=0.05,
            max_rps=1000, max_tokens_per_minute=1_000_000,
            allowed_backends=["echo"],
        ),
    ])

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = FilesystemStore(tmp)
        app = build_gateway(
            engine=engine, signer=kp, cert_store=store,
            backend_registry=BackendRegistry([EchoBackend()]),
            tenant_store=tenants,
        )
        client = TestClient(app)

        # 1. Issue a chat completion.
        r = client.post(
            "/v1/chat/completions",
            headers={"X-TrustLens-Tenant-Id": "demo"},
            json={
                "model": "echo",
                "messages": [
                    {"role": "user",
                     "content": "What is the capital of France?"},
                ],
            },
        )
        r.raise_for_status()
        body = r.json()
        cert_id = body["trustlens"]["certificate_id"]
        print("certificate_id:", cert_id)
        print("certificate_status:", body["trustlens"]["certificate_status"])

        # 2. Re-verify the cert server-side.
        v = client.post("/v1/verify", json={"cert_id": cert_id})
        v.raise_for_status()
        print("verify:", json.dumps(v.json(), indent=2))

        # 3. Same check offline, with just the public key.
        cert = store.get(cert_id)
        assert cert is not None
        offline = verify_certificate(cert, kp.public_key)
        print("offline.valid:", offline.valid)


if __name__ == "__main__":
    main()

"""Stateless HTTP verifier service.

Exposes a small FastAPI surface:
    POST /v1/verify      — accepts prompt + response, returns a signed cert
    GET  /v1/cert/{id}   — retrieves a previously-issued cert
    GET  /healthz        — liveness
    GET  /readyz         — readiness (oracles + signer reachable)
    GET  /metrics        — Prometheus metrics

Horizontally scalable: no in-process state beyond the oracle cache and the
signer keypair.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from trustlens.certificate.schema import Certificate, CertificatePayload
from trustlens.certificate.signer import KeyPair, sign_certificate
from trustlens.certificate.store import CertificateStore, FilesystemStore
from trustlens.oracles.registry import OracleRegistry, OracleSelection
from trustlens.robustness.circuit_breaker import CircuitBreaker
from trustlens.robustness.deadline import Deadline
from trustlens.robustness.shadow_eval import ShadowEvalSampler
from trustlens.verifier.engine import (
    VerificationRequest,
    VerificationResult,
    VerifierEngine,
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class VerifyBody(BaseModel):
    prompt: str
    response: str
    tenant_id: str
    model_id: str = "unknown"
    request_id: Optional[str] = None
    tau: float = 0.6
    tau_prime: float = 0.3
    oracles: Optional[list[str]] = None
    deadline_ms: int = 500


class VerifyReply(BaseModel):
    certificate: Certificate
    renderable_text: str
    masked_claim_ids: list[str]
    oracle_latencies_ms: dict[str, float]
    pipeline_latency_ms: float


class HealthReply(BaseModel):
    status: str
    pipeline_version: str


# ---------------------------------------------------------------------------
# Service factory
# ---------------------------------------------------------------------------

def build_app(
    engine: VerifierEngine,
    signer: KeyPair,
    store: CertificateStore,
    *,
    shadow_sampler: Optional[ShadowEvalSampler] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> FastAPI:
    app = FastAPI(title="TrustLens Verifier", version="1.0.0")
    shadow = shadow_sampler or ShadowEvalSampler(sample_rate=0.01)
    breaker = circuit_breaker or CircuitBreaker(failure_threshold=20, recovery_time_s=30)

    # Lazy observability import (keeps the module importable without prom installed)
    try:
        from trustlens.observability.metrics import Metrics
        metrics = Metrics()
    except Exception:
        metrics = None

    @app.get("/healthz", response_model=HealthReply)
    async def healthz() -> HealthReply:
        from trustlens.version import PIPELINE_VERSION
        return HealthReply(status="ok", pipeline_version=PIPELINE_VERSION)

    @app.get("/readyz")
    async def readyz() -> dict:
        if not breaker.allow():
            raise HTTPException(status_code=503, detail="circuit_open")
        return {"status": "ready", "oracles": engine._oracles.names()}  # type: ignore[attr-defined]

    @app.post("/v1/verify", response_model=VerifyReply)
    async def verify(body: VerifyBody, request: Request) -> VerifyReply:
        if not breaker.allow():
            raise HTTPException(status_code=503, detail="circuit_open")

        req_id = body.request_id or request.headers.get("x-request-id") or str(uuid.uuid4())
        deadline = Deadline(body.deadline_ms)

        oracles = body.oracles or engine._oracles.names()  # type: ignore[attr-defined]
        selection = OracleSelection(
            priority_order=oracles,
            deadline_ms=min(body.deadline_ms, 250),
        )

        t0 = time.perf_counter()
        try:
            vreq = VerificationRequest(
                prompt=body.prompt,
                response_text=body.response,
                tenant_id=body.tenant_id,
                request_id=req_id,
                model_id=body.model_id,
                tau=body.tau,
                tau_prime=body.tau_prime,
                oracle_selection=selection,
            )
            result: VerificationResult = await engine.verify(vreq)
        except Exception as e:
            breaker.record_failure()
            if metrics:
                metrics.verifier_errors_total.labels(kind=type(e).__name__).inc()
            raise HTTPException(status_code=500, detail=f"verify_failed: {e}") from e

        breaker.record_success()
        pipeline_ms = (time.perf_counter() - t0) * 1000.0

        # Mark shadow-eval sampling flag
        sampled = shadow.should_sample(body.tenant_id, req_id)
        result.payload.shadow_eval_sampled = sampled

        # Sign and persist
        cert = sign_certificate(result.payload, signer)
        try:
            store.put(cert)
        except Exception:
            # Storage failures must not block response — they are eventually
            # consistent from the audit bucket's perspective.
            if metrics:
                metrics.certificate_store_errors_total.inc()

        if metrics:
            metrics.verify_latency_ms.labels(tenant=body.tenant_id).observe(pipeline_ms)
            metrics.verify_requests_total.labels(
                tenant=body.tenant_id,
                status=result.payload.overall_status.value,
            ).inc()
            for name, latency in result.oracle_latencies_ms.items():
                metrics.oracle_latency_ms.labels(oracle=name).observe(latency)

        if sampled:
            shadow.submit(cert, result.renderable_text)

        return VerifyReply(
            certificate=cert,
            renderable_text=result.renderable_text,
            masked_claim_ids=result.masked_claim_ids,
            oracle_latencies_ms=result.oracle_latencies_ms,
            pipeline_latency_ms=pipeline_ms,
        )

    @app.get("/v1/cert/{cert_id}", response_model=Certificate)
    async def get_cert(cert_id: str) -> Certificate:
        cert = store.get(cert_id)
        if cert is None:
            raise HTTPException(status_code=404, detail="not_found")
        return cert

    if metrics is not None:
        @app.get("/metrics")
        async def metrics_endpoint():
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(
                metrics.render(), media_type="text/plain; version=0.0.4"
            )

    return app


# ---------------------------------------------------------------------------
# uvicorn entrypoint for `python -m trustlens.verifier.service`
# ---------------------------------------------------------------------------

def _default_app() -> FastAPI:
    """Build a default app from environment variables.

    TRUSTLENS_SIGNER_KEY   — path to Ed25519 private PEM (generated if absent)
    TRUSTLENS_CERT_STORE   — path to certificate store directory
    TRUSTLENS_ORACLES      — comma-separated oracle names: wikidata,customer_kb
    """
    from pathlib import Path
    from trustlens.oracles.wikidata import WikidataOracle
    from trustlens.oracles.customer_kb import CustomerKBOracle, LexicalKBIndex

    key_path = Path(os.environ.get("TRUSTLENS_SIGNER_KEY", "./.trustlens/signer.pem"))
    store_path = Path(os.environ.get("TRUSTLENS_CERT_STORE", "./.trustlens/certs"))
    enabled = set(
        os.environ.get("TRUSTLENS_ORACLES", "wikidata").split(",")
    )

    # Load or generate keypair
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        keypair = KeyPair.from_private_pem(key_path.read_bytes())
    else:
        keypair = KeyPair.generate()
        key_path.write_bytes(keypair.private_pem())

    # Build oracles
    oracles = []
    if "wikidata" in enabled:
        oracles.append(WikidataOracle())
    if "customer_kb" in enabled:
        oracles.append(CustomerKBOracle(LexicalKBIndex()))
    registry = OracleRegistry(oracles=oracles)
    engine = VerifierEngine(registry)
    store = FilesystemStore(store_path)
    return build_app(engine, keypair, store)


app = _default_app() if os.environ.get("TRUSTLENS_AUTOSTART") == "1" else None


def main() -> None:
    import uvicorn
    app_ = _default_app()
    uvicorn.run(app_, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()

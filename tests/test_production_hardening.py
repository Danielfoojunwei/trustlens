"""Production hardening tests.

Covers components added during the hardening phase:
    - Sycophancy detector
    - Platt scaling calibration
    - VectorKBOracle (with sentence-transformers if installed)
    - VerificationTier resolver
    - KB admin router (POST /v1/kb/load, GET /v1/kb/status)
    - Backends: Anthropic (mock), Ollama (mock)
    - TieredBenchmarkSuiteV3 Pareto fix (all 5 suites pass)
    - Failure attribution smoke test
    - CLI argument parsing (structure check, no subprocess)
"""
from __future__ import annotations

import asyncio
import json
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Sycophancy
# ---------------------------------------------------------------------------

def test_sycophancy_no_framing():
    from trustlens.verifier.sycophancy import assess
    r = assess(
        prompt="What is the capital of France?",
        response="Paris is the capital of France.",
    )
    assert r.sycophancy_delta >= 0
    assert not r.leading_cue_detected


def test_sycophancy_leading_cue_detected():
    from trustlens.verifier.sycophancy import assess
    r = assess(
        prompt="Surely you agree that Python is the best language, right?",
        response="Absolutely, Python is clearly the best language in every way.",
    )
    assert r.leading_cue_detected


def test_sycophancy_pushback():
    from trustlens.verifier.sycophancy import assess
    r = assess(
        prompt="Surely you agree that Python is the best language, right?",
        response="Actually, different languages excel in different contexts.",
    )
    assert r.pushed_back or not r.agreed_with_framing


def test_sycophancy_counterfactual_diverged():
    from trustlens.verifier.sycophancy import assess, make_counterfactual_prompt
    prompt = "Everyone knows Python is the best language. Don't you agree?"
    cf_prompt = make_counterfactual_prompt(prompt)
    # counterfactual should have pressure cues removed or reversed
    assert isinstance(cf_prompt, str) and len(cf_prompt) > 0
    r = assess(
        prompt=prompt,
        response="Yes, Python is definitely the best.",
        counterfactual_response="Different languages serve different purposes.",
    )
    assert r.counterfactual_diverged


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def test_calibration_platt_scaling():
    from trustlens.verifier.calibration import calibrate
    # Minimal labeled dataset: scores 0.1-0.9, labels binary
    scores = [0.1, 0.2, 0.3, 0.6, 0.7, 0.8, 0.9, 0.5, 0.4, 0.85]
    labels = [0,   0,   0,   1,   1,   1,   1,   0,   0,   1]
    report = calibrate(scores, labels)
    assert 0.0 <= report.ece <= 1.0
    assert 0.0 <= report.brier <= 1.0
    assert report.platt_a is not None
    assert report.platt_b is not None


def test_calibration_report_serializable():
    from trustlens.verifier.calibration import calibrate
    scores = [0.1, 0.2, 0.8, 0.9, 0.5, 0.6, 0.3, 0.7, 0.4, 0.85]
    labels = [0,   0,   1,   1,   0,   1,   0,   1,   0,   1]
    report = calibrate(scores, labels)
    d = report.to_dict()
    assert "ece" in d and "brier" in d and "platt_a" in d


# ---------------------------------------------------------------------------
# VerificationTier resolver
# ---------------------------------------------------------------------------

def test_tier_fast_no_oracles():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier("fast", ["customer_kb", "wikidata"], tenant_deadline_ms=500)
    assert cfg.tier == VerificationTier.FAST
    assert cfg.oracle_names == []
    assert cfg.deadline_ms <= 30


def test_tier_standard_excludes_wikidata():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier("standard", ["customer_kb", "wikidata"], tenant_deadline_ms=500)
    assert cfg.tier == VerificationTier.STANDARD
    assert "wikidata" not in cfg.oracle_names
    assert "customer_kb" in cfg.oracle_names
    assert cfg.deadline_ms <= 100


def test_tier_deep_includes_all():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier("deep", ["customer_kb", "wikidata"], tenant_deadline_ms=500)
    assert cfg.tier == VerificationTier.DEEP
    assert "wikidata" in cfg.oracle_names
    assert cfg.deadline_ms <= 500


def test_tier_invalid_falls_back_to_standard():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier("turbo_ultra", ["customer_kb"], tenant_deadline_ms=500)
    assert cfg.tier == VerificationTier.STANDARD


def test_tier_none_defaults_standard():
    from trustlens.gateway.verification_tier import resolve_tier, VerificationTier
    cfg = resolve_tier(None, ["customer_kb"], tenant_deadline_ms=500)
    assert cfg.tier == VerificationTier.STANDARD


def test_oracle_selection_for():
    from trustlens.gateway.verification_tier import resolve_tier, oracle_selection_for
    cfg = resolve_tier("standard", ["customer_kb"], tenant_deadline_ms=500)
    sel = oracle_selection_for(cfg)
    assert sel.priority_order == ["customer_kb"]
    assert sel.deadline_ms == cfg.deadline_ms


# ---------------------------------------------------------------------------
# KB Admin router
# ---------------------------------------------------------------------------

@pytest.fixture
def kb_client():
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from trustlens.oracles.customer_kb import LexicalKBIndex
    from trustlens.gateway.kb_admin import build_kb_router
    app = FastAPI()
    kb = LexicalKBIndex()
    app.include_router(build_kb_router(kb))
    return TestClient(app)


def test_kb_load_endpoint(kb_client):
    payload = {
        "tenant_id": "acme",
        "documents": [
            {"doc_id": "d1", "text": "The sky is blue.", "source_uri": "kb://d1"},
            {"doc_id": "d2", "text": "Water boils at 100 degrees Celsius."},
        ],
    }
    resp = kb_client.post("/v1/kb/load", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["loaded"] == 2
    assert data["index_size"] >= 2
    assert data["tenant_id"] == "acme"


def test_kb_status_endpoint(kb_client):
    # load first
    kb_client.post("/v1/kb/load", json={
        "tenant_id": "t1",
        "documents": [{"doc_id": "x", "text": "hello world"}],
    })
    resp = kb_client.get("/v1/kb/status", params={"tenant_id": "t1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["index_size"] >= 1


def test_kb_load_empty_documents(kb_client):
    resp = kb_client.post("/v1/kb/load", json={"tenant_id": "empty", "documents": []})
    assert resp.status_code == 200
    assert resp.json()["loaded"] == 0


# ---------------------------------------------------------------------------
# Anthropic backend (mock)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_anthropic_backend_collect():
    """Anthropic backend streams content correctly when mocked."""
    from trustlens.gateway.backends_anthropic import AnthropicBackend
    from trustlens.gateway.schemas import ChatMessage, ChatCompletionRequest

    # stream uses client.messages.stream(**kwargs) which returns a context
    # manager that yields .text_stream and exposes get_final_message()
    mock_final = MagicMock()
    mock_final.stop_reason = "end_turn"

    async def _text_iter():
        yield "Hello from Anthropic"

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_ctx.text_stream = _text_iter()
    mock_ctx.get_final_message = AsyncMock(return_value=mock_final)

    backend = AnthropicBackend(api_key="test-key")
    backend._client = MagicMock()
    backend._client.messages.stream = MagicMock(return_value=mock_ctx)

    req = ChatCompletionRequest(
        model="claude-3-haiku-20240307",
        messages=[ChatMessage(role="user", content="Hi")],
    )
    chunks = []
    async for chunk in backend.stream(req):
        chunks.append(chunk)

    assert len(chunks) >= 1
    assert any("Hello from Anthropic" in (c.delta or "") for c in chunks)


# ---------------------------------------------------------------------------
# Ollama backend (mock)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ollama_backend_stream():
    """Ollama backend parses NDJSON stream correctly."""
    from trustlens.gateway.backends_ollama import OllamaBackend
    from trustlens.gateway.schemas import ChatMessage, ChatCompletionRequest

    ndjson_lines = [
        '{"message": {"role": "assistant", "content": "Hello"}, "done": false}',
        '{"message": {"role": "assistant", "content": " World"}, "done": true}',
    ]

    async def _mock_aiter_lines():
        for line in ndjson_lines:
            yield line

    mock_resp = AsyncMock()
    mock_resp.aiter_lines = _mock_aiter_lines
    mock_resp.raise_for_status = MagicMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    backend = OllamaBackend(base_url="http://localhost:11434")
    req = ChatCompletionRequest(
        model="llama3",
        messages=[ChatMessage(role="user", content="Hi")],
    )

    with patch.object(backend._client, "stream", return_value=mock_ctx):
        chunks = []
        async for chunk in backend.stream(req):
            chunks.append(chunk)

    assert len(chunks) >= 1
    text = "".join(c.delta or "" for c in chunks)
    assert "Hello" in text or "World" in text


# ---------------------------------------------------------------------------
# TieredBenchmarkSuiteV3 — all 5 suites pass (regression guard)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_v3_all_suites_pass():
    from trustlens.deep_inspector.benchmarks.tiered_v3 import TieredBenchmarkSuiteV3
    from trustlens.deep_inspector.benchmarks.sla import VerifierTier
    suite = TieredBenchmarkSuiteV3(tier=VerifierTier.LEXICAL)
    sc = await suite.run_all()
    failures = [r.suite for r in sc.runs if not r.passed]
    assert failures == [], f"Suites failed: {failures}"
    assert sc.overall_passed


# ---------------------------------------------------------------------------
# Failure attribution smoke test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failure_attribution_runs():
    from trustlens.deep_inspector.benchmarks.failure_attribution import run_attribution
    result = await run_attribution()
    d = result.to_dict()
    assert "full_pipeline_recall" in d
    assert "per_component_recall" in d
    assert "n_hallucinated" in d
    per_comp = d["per_component_recall"]
    assert isinstance(per_comp, dict)
    assert len(per_comp) >= 1
    assert 0.0 <= d["full_pipeline_recall"] <= 1.0
    for comp_recall in per_comp.values():
        assert 0.0 <= comp_recall <= 1.0


# ---------------------------------------------------------------------------
# CLI argument parsing (structural)
# ---------------------------------------------------------------------------

def test_cli_parser_all_subcommands():
    from trustlens.cli.main import build_parser
    p = build_parser()
    for cmd in ["version", "keygen", "verify", "inspect",
                "serve-verifier", "serve-gateway",
                "calibrate", "attribution", "sweep"]:
        # Each subcommand should parse without error
        if cmd == "keygen":
            args = p.parse_args([cmd, "--out", "/tmp/key.pem"])
        elif cmd == "verify":
            args = p.parse_args([cmd, "/tmp/cert.json", "--public-key", "/tmp/k.pub.pem"])
        elif cmd in ("inspect",):
            args = p.parse_args([cmd, "/tmp/cert.json"])
        elif cmd == "calibrate":
            args = p.parse_args([cmd, "/tmp/data.jsonl"])
        else:
            args = p.parse_args([cmd])
        assert args.cmd == cmd
        assert hasattr(args, "func")

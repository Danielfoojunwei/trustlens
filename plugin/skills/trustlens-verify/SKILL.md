---
name: trustlens-verify
description: Verify an LLM response or an existing certificate. Use whenever the user asks to "check this answer", "is this hallucinated", "verify the model said X", or wants to inspect a saved certificate.
---

# TrustLens verification

## When asked to verify a *response* (model just answered)

1. Confirm with the user which **tier** to use (default `standard`):
   - `fast` — NLI-only, no oracle calls. Sub-30 ms. Use when latency
     matters more than grounding.
   - `standard` — NLI + KB oracle. Default for production traffic.
   - `deep` — NLI + KB + Wikidata + Deep Inspector. Use when stakes are
     high (compliance, agentic).

2. Call `chat(prompt=..., model=..., tier=...)`. The tool returns the
   response *and* the signed certificate annotation.

3. Read the certificate to the user:
   - `certificate_status`: VERIFIED / PARTIAL / BLOCKED / DEGRADED
   - `masked_claim_ids`: which claims the gateway suppressed
   - For each claim from `verify_certificate(certificate_id)`:
     - `verdict`: VERIFIED / UNCERTAIN / UNSUPPORTED / CONTRADICTED
     - `support_mass`, `contradiction_mass`
     - `oracle_receipts`: which docs grounded the claim

4. If the cert was BLOCKED, explain *why* (read the
   `oracle_receipts` for the rejected claim) and offer:
   - `kb_upsert` to add a grounding document
   - `settings_update(ssh_threshold_rho=...)` to loosen the threshold
   - `incidents_list` to see if a wider issue is happening

## When asked to verify an *existing* certificate

`verify_certificate(certificate_id)` returns the full cert + signature
status. Tell the user:
- whether the signature is valid
- which signer key signed it
- the `pipeline_version` (proves *what code* produced the verdict)
- any masked claims

## What VERIFIED actually means

A claim landing in VERIFIED proves three things:
1. At least one oracle returned evidence (with cited `source_uri`).
2. The NLI stack agreed (entailment).
3. Aggregate `support_mass >= tau` (per-tenant threshold).

It does **not** prove the model "knows" anything — it proves that the
specific atomic claim it emitted is grounded in your verifier's evidence
sources at the moment the certificate was signed.

## Honest limitations to convey when relevant

- LEXICAL tier: 1.25–2× hallucination reduction vs raw LLM (measured).
- DEEP tier: 2.5–4× reduction (gate-based; reproduce on user's corpus).
- Verifier won't catch hallucinations in *non-extractable* claims
  (e.g. opinions). It tags them UNCERTAIN.

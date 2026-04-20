# TrustLens — universal LLM safety layer.
#
# Multi-stage build. Stage 1 installs build deps; stage 2 is a slim runtime.
# Optional CUDA layer extends with torch+transformers for the real Deep
# Inspector adapters and TransformerNLI.

ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE} AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY trustlens ./trustlens
RUN pip install --no-cache-dir build && python -m build --wheel

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS runtime

ARG WITH_TRANSFORMERS=0
LABEL org.opencontainers.image.title="trustlens"
LABEL org.opencontainers.image.description="Universal LLM safety layer with signed trust certificates."
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app
COPY --from=builder /build/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl uvicorn[standard] \
 && if [ "$WITH_TRANSFORMERS" = "1" ]; then \
        pip install --no-cache-dir transformers sentence-transformers torch ; \
    fi \
 && rm /tmp/*.whl

ENV TRUSTLENS_HOST=0.0.0.0 \
    TRUSTLENS_PORT=7700 \
    TRUSTLENS_SIGNER_KEY=/data/signer.pem \
    TRUSTLENS_CERT_STORE=/data/certs \
    TRUSTLENS_WORKERS=2

VOLUME ["/data"]
EXPOSE 7700

# Liveness probe — gateway must report its signer + cert store + backend
# registry healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:${TRUSTLENS_PORT}/healthz', timeout=3); sys.exit(0 if r.status==200 else 1)" || exit 1

# Default command: uvicorn with multiple workers — a single worker is fine
# for the echo demo, but real upstreams benefit from --workers >= 2. Operators
# may override with TRUSTLENS_WORKERS.
CMD ["sh", "-c", "exec uvicorn trustlens.gateway.app:build_gateway_from_env --factory --host ${TRUSTLENS_HOST} --port ${TRUSTLENS_PORT} --workers ${TRUSTLENS_WORKERS}"]

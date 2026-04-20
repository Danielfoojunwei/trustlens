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
    TRUSTLENS_CERT_STORE=/data/certs

VOLUME ["/data"]
EXPOSE 7700

# Default command: gateway in demo mode (echo backend, demo tenant).
# Override TRUSTLENS_BACKEND_URL / OPENAI_API_KEY etc. for real upstreams.
CMD ["sh", "-c", "trustlens serve-gateway --host ${TRUSTLENS_HOST} --port ${TRUSTLENS_PORT} --signer-key ${TRUSTLENS_SIGNER_KEY} --cert-store ${TRUSTLENS_CERT_STORE}"]

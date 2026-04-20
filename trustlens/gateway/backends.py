"""Upstream LLM backends.

The gateway proxies requests to a customer-configured upstream. Every backend
conforms to an async interface: submit a chat request, return the completion
(streaming or buffered). TrustLens verification happens around the backend,
not inside it — this keeps the adapter layer thin.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Protocol

import httpx
from tenacity import (
    AsyncRetrying, retry_if_exception_type, stop_after_attempt,
    wait_exponential,
)

from trustlens.gateway.schemas import ChatCompletionRequest, ChatMessage
from trustlens.utils.redact import redact_secrets


logger = logging.getLogger(__name__)


_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def _is_retryable_http_status(resp: httpx.Response) -> bool:
    return resp.status_code in (429, 500, 502, 503, 504)


@dataclass
class BackendResponse:
    """A buffered (non-streaming) completion from an upstream backend."""
    content: str
    model: str
    finish_reason: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict = None  # type: ignore[assignment]


@dataclass
class BackendStreamChunk:
    """One delta from a streaming completion."""
    delta: str
    finish_reason: Optional[str] = None


class Backend(Protocol):
    """Backend interface."""

    name: str

    async def complete(self, req: ChatCompletionRequest) -> BackendResponse: ...
    async def stream(
        self, req: ChatCompletionRequest
    ) -> AsyncIterator[BackendStreamChunk]: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Echo backend — for tests and local dev. Returns a deterministic completion
# formed from the last user message. No network calls.
# ---------------------------------------------------------------------------

class EchoBackend:
    name = "echo"

    async def complete(self, req: ChatCompletionRequest) -> BackendResponse:
        last_user = next(
            (m.content for m in reversed(req.messages) if m.role == "user"), ""
        )
        content = f"Echo: {last_user}"
        return BackendResponse(
            content=content,
            model=req.model or "echo",
            finish_reason="stop",
            prompt_tokens=sum(len(m.content) // 4 for m in req.messages),
            completion_tokens=len(content) // 4,
            raw={},
        )

    async def stream(
        self, req: ChatCompletionRequest
    ) -> AsyncIterator[BackendStreamChunk]:
        full = (await self.complete(req)).content
        # Chunk on whitespace so the consumer sees multiple deltas
        for i, tok in enumerate(full.split(" ")):
            prefix = " " if i > 0 else ""
            yield BackendStreamChunk(delta=prefix + tok)
        yield BackendStreamChunk(delta="", finish_reason="stop")

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# OpenAI-compatible backend — works with OpenAI, Together, Anyscale, vLLM's
# OpenAI-compat server, Ollama (with openai-compat plugin), etc.
# ---------------------------------------------------------------------------

class OpenAICompatBackend:
    """Generic OpenAI-compatible HTTP backend."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: Optional[str] = None,
        timeout_s: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
        max_retries: int = 3,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._max_retries = max_retries
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
        )
        self._owns_client = client is None

    async def complete(self, req: ChatCompletionRequest) -> BackendResponse:
        body = self._body(req, stream=False)

        async def _post_once() -> httpx.Response:
            r = await self._client.post(
                f"{self.base_url}/chat/completions", json=body
            )
            if _is_retryable_http_status(r):
                raise httpx.HTTPStatusError(
                    f"retryable {r.status_code}", request=r.request, response=r,
                )
            return r

        r: Optional[httpx.Response] = None
        async for attempt in AsyncRetrying(
            reraise=True,
            stop=stop_after_attempt(max(1, self._max_retries)),
            wait=wait_exponential(multiplier=0.25, min=0.25, max=4.0),
            retry=retry_if_exception_type(_RETRYABLE_EXC + (httpx.HTTPStatusError,)),
        ):
            with attempt:
                try:
                    r = await _post_once()
                except Exception as e:
                    logger.warning(
                        "backend=%s retryable attempt=%d err=%s",
                        self.name, attempt.retry_state.attempt_number,
                        redact_secrets(type(e).__name__),
                    )
                    raise
        assert r is not None
        r.raise_for_status()
        data = r.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = data.get("usage", {})
        return BackendResponse(
            content=str(msg.get("content", "")),
            model=data.get("model", req.model),
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            raw=data,
        )

    async def stream(
        self, req: ChatCompletionRequest
    ) -> AsyncIterator[BackendStreamChunk]:
        body = self._body(req, stream=True)
        async with self._client.stream(
            "POST", f"{self.base_url}/chat/completions", json=body
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    yield BackendStreamChunk(delta="", finish_reason="stop")
                    return
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {}).get("content", "") or ""
                finish = choice.get("finish_reason")
                yield BackendStreamChunk(delta=delta, finish_reason=finish)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _body(self, req: ChatCompletionRequest, stream: bool) -> dict:
        out = {
            "model": req.model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": stream,
        }
        if req.temperature is not None:
            out["temperature"] = req.temperature
        if req.top_p is not None:
            out["top_p"] = req.top_p
        if req.max_tokens is not None:
            out["max_tokens"] = req.max_tokens
        if req.user is not None:
            out["user"] = req.user
        return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class BackendRegistry:
    """Maps (tenant_allowed_backends × model_hint) → Backend instance."""

    def __init__(self, backends: Optional[list[Backend]] = None):
        self._by_name: dict[str, Backend] = {
            b.name: b for b in (backends or [])
        }

    def register(self, backend: Backend) -> None:
        self._by_name[backend.name] = backend

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def get(self, name: str) -> Optional[Backend]:
        return self._by_name.get(name)

    def select(
        self, model: str, allowed: list[str]
    ) -> Optional[Backend]:
        """Pick a backend.

        Priority:
            1. Exact match on `model` containing a backend name (e.g. "openai/gpt-4")
            2. First allowed backend registered
        """
        for name in allowed:
            if name in self._by_name and name in model.lower():
                return self._by_name[name]
        for name in allowed:
            if name in self._by_name:
                return self._by_name[name]
        return None

    async def close_all(self) -> None:
        for b in self._by_name.values():
            try:
                await b.close()
            except Exception as e:
                logger.warning("backend close failed name=%s err=%s",
                               getattr(b, "name", "?"), type(e).__name__)

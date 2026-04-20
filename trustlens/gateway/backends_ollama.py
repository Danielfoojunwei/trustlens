"""Real Ollama backend — calls a local Ollama instance via /api/chat.

Ollama exposes both `/api/chat` (native) and `/v1/chat/completions`
(OpenAI-compat). This backend uses the native endpoint to avoid any double
translation, and to access Ollama-specific knobs like `keep_alive`.

`httpx` is already a hard dependency.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Optional

import httpx

from trustlens.gateway.backends import BackendResponse, BackendStreamChunk
from trustlens.gateway.schemas import ChatCompletionRequest


class OllamaBackend:
    """Ollama backend (native /api/chat)."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        keep_alive: str = "5m",
        timeout_s: float = 60.0,
    ):
        self._base = base_url.rstrip("/")
        self._keep_alive = keep_alive
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=5.0),
        )

    async def complete(self, req: ChatCompletionRequest) -> BackendResponse:
        body = self._body(req, stream=False)
        r = await self._client.post(f"{self._base}/api/chat", json=body)
        r.raise_for_status()
        data = r.json()
        msg = data.get("message", {})
        return BackendResponse(
            content=str(msg.get("content", "")),
            model=data.get("model", req.model),
            finish_reason=data.get("done_reason"),
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            completion_tokens=int(data.get("eval_count", 0)),
            raw=data,
        )

    async def stream(
        self, req: ChatCompletionRequest
    ) -> AsyncIterator[BackendStreamChunk]:
        body = self._body(req, stream=True)
        async with self._client.stream(
            "POST", f"{self._base}/api/chat", json=body
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = chunk.get("message", {})
                delta = str(msg.get("content", ""))
                done = bool(chunk.get("done"))
                yield BackendStreamChunk(
                    delta=delta,
                    finish_reason="stop" if done else None,
                )
                if done:
                    return

    async def close(self) -> None:
        await self._client.aclose()

    def _body(self, req: ChatCompletionRequest, stream: bool) -> dict:
        out: dict = {
            "model": req.model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": stream,
            "keep_alive": self._keep_alive,
        }
        opts: dict = {}
        if req.temperature is not None:
            opts["temperature"] = req.temperature
        if req.top_p is not None:
            opts["top_p"] = req.top_p
        if req.max_tokens is not None:
            opts["num_predict"] = req.max_tokens
        if opts:
            out["options"] = opts
        return out

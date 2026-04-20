"""Real Anthropic backend — uses the official `anthropic` SDK.

Implements the Backend Protocol so the gateway can route to Claude models
via Anthropic's Messages API. Both buffered and streaming completions.

`anthropic` is an optional dependency — imported lazily.
"""

from __future__ import annotations

import os
from typing import AsyncIterator, Optional

from trustlens.gateway.backends import BackendResponse, BackendStreamChunk
from trustlens.gateway.schemas import ChatCompletionRequest, ChatMessage


class AnthropicBackend:
    """Anthropic Messages API backend."""

    name = "anthropic"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_s: float = 30.0,
    ):
        from anthropic import AsyncAnthropic  # type: ignore
        self._client = AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            base_url=base_url,
            timeout=timeout_s,
        )

    async def complete(self, req: ChatCompletionRequest) -> BackendResponse:
        system_msgs, conv = _split_system(req.messages)
        kwargs = self._build_kwargs(req, system_msgs, conv)
        msg = await self._client.messages.create(**kwargs)
        text = "".join(
            block.text for block in msg.content
            if getattr(block, "type", "") == "text"
        )
        return BackendResponse(
            content=text,
            model=msg.model,
            finish_reason=msg.stop_reason,
            prompt_tokens=getattr(msg.usage, "input_tokens", 0),
            completion_tokens=getattr(msg.usage, "output_tokens", 0),
            raw=msg.model_dump() if hasattr(msg, "model_dump") else {},
        )

    async def stream(
        self, req: ChatCompletionRequest
    ) -> AsyncIterator[BackendStreamChunk]:
        system_msgs, conv = _split_system(req.messages)
        kwargs = self._build_kwargs(req, system_msgs, conv)
        async with self._client.messages.stream(**kwargs) as stream:
            async for chunk in stream.text_stream:
                yield BackendStreamChunk(delta=chunk)
            final = await stream.get_final_message()
            yield BackendStreamChunk(delta="", finish_reason=final.stop_reason)

    async def close(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------
    def _build_kwargs(
        self,
        req: ChatCompletionRequest,
        system_msgs: list[str],
        conv: list[dict],
    ) -> dict:
        kw = {
            "model": req.model,
            "max_tokens": req.max_tokens or 1024,
            "messages": conv,
        }
        if system_msgs:
            kw["system"] = "\n\n".join(system_msgs)
        if req.temperature is not None:
            kw["temperature"] = req.temperature
        if req.top_p is not None:
            kw["top_p"] = req.top_p
        return kw


def _split_system(msgs: list[ChatMessage]) -> tuple[list[str], list[dict]]:
    """Anthropic separates `system` from the message list."""
    system: list[str] = []
    conv: list[dict] = []
    for m in msgs:
        if m.role == "system":
            system.append(m.content)
        elif m.role in ("user", "assistant"):
            conv.append({"role": m.role, "content": m.content})
        # tool messages skipped — Anthropic uses tool_use blocks, out of
        # scope for this backend.
    return system, conv

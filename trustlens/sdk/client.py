"""TrustLens Python SDK.

One-line retrofit for existing OpenAI-style code:

    from trustlens import TrustLens
    client = TrustLens(base_url="https://gateway.trustlens.ai", api_key="sk-...")
    result = client.chat.completions.create(
        model="openai/gpt-4o",
        messages=[{"role": "user", "content": "What is the capital of France?"}],
        trustlens={"tenant_id": "acme-corp"},
    )
    print(result.content)          # the verified output
    print(result.certificate_id)   # audit pointer
    print(result.is_verified)      # True if cert_status == "verified"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator, Optional

import httpx


@dataclass
class VerifiedCompletion:
    """A single non-streaming completion plus its TrustLens annotation."""
    content: str
    model: str
    certificate_id: Optional[str] = None
    certificate_status: Optional[str] = None
    masked_claim_ids: list[str] = field(default_factory=list)
    degradations: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def is_verified(self) -> bool:
        return self.certificate_status == "verified"

    @property
    def is_blocked(self) -> bool:
        return self.certificate_status == "blocked"


class _Completions:
    def __init__(self, client: "TrustLens") -> None:
        self._c = client

    def create(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool = False,
        trustlens: Optional[dict] = None,
        **kwargs,
    ):
        if stream:
            return self._stream(model=model, messages=messages, trustlens=trustlens, **kwargs)
        body = self._c._body(model, messages, stream=False, trustlens=trustlens, **kwargs)
        r = self._c._http.post(
            f"{self._c.base_url}/v1/chat/completions",
            json=body,
            headers=self._c._headers(),
        )
        r.raise_for_status()
        return _parse_completion(r.json())

    def _stream(
        self,
        *,
        model: str,
        messages: list[dict],
        trustlens: Optional[dict],
        **kwargs,
    ) -> Iterator[dict]:
        body = self._c._body(model, messages, stream=True, trustlens=trustlens, **kwargs)
        with self._c._http.stream(
            "POST", f"{self._c.base_url}/v1/chat/completions",
            json=body, headers=self._c._headers(),
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                s = line if isinstance(line, str) else line.decode("utf-8")
                if not s.startswith("data:"):
                    continue
                data = s[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue


class _Chat:
    def __init__(self, client: "TrustLens") -> None:
        self.completions = _Completions(client)


class TrustLens:
    """Synchronous client."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        timeout_s: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._tenant_id = tenant_id
        self._http = httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5.0))
        self.chat = _Chat(self)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        if self._tenant_id:
            h["X-TrustLens-Tenant-Id"] = self._tenant_id
        return h

    def _body(
        self,
        model: str,
        messages: list[dict],
        stream: bool,
        trustlens: Optional[dict],
        **kwargs,
    ) -> dict:
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        body.update({k: v for k, v in kwargs.items() if v is not None})
        if trustlens or self._tenant_id:
            body["trustlens"] = dict(trustlens or {})
            if self._tenant_id and "tenant_id" not in body["trustlens"]:
                body["trustlens"]["tenant_id"] = self._tenant_id
        return body

    def get_certificate(self, cert_id: str) -> dict:
        """Fetch a previously-issued certificate by id."""
        r = self._http.get(
            f"{self.base_url}/v1/cert/{cert_id}", headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "TrustLens":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _parse_completion(data: dict) -> VerifiedCompletion:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {})
    tl = data.get("trustlens") or {}
    return VerifiedCompletion(
        content=str(msg.get("content", "")),
        model=data.get("model", ""),
        certificate_id=tl.get("certificate_id"),
        certificate_status=tl.get("certificate_status"),
        masked_claim_ids=list(tl.get("masked_claim_ids") or []),
        degradations=list(tl.get("degradations") or []),
        usage=data.get("usage") or {},
        raw=data,
    )

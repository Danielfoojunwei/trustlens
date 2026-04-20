"""OpenAI-compatible schemas (+ TrustLens extensions).

The gateway speaks the OpenAI `/v1/chat/completions` wire format so customers
can switch with a one-line base_url change. TrustLens-specific fields live
under `trustlens` (request-side) and as a response header + body extension
(response-side) so OpenAI-only SDKs ignore them cleanly.
"""

from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict

from trustlens.certificate.schema import Certificate


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: Optional[str] = None


class TrustLensExtras(BaseModel):
    """Opt-in TrustLens-specific request fields."""
    model_config = ConfigDict(extra="forbid")

    tenant_id: Optional[str] = None           # required in multi-tenant deployment
    verify: bool = True                        # run verification on the response
    allow_unsupported: bool = False            # include unsupported claims (opt-in)
    tau: Optional[float] = None                # override tenant default
    tau_prime: Optional[float] = None
    oracles: Optional[list[str]] = None        # override tenant default
    deadline_ms: Optional[int] = None
    deep_inspector: bool = False               # require Deep Inspector tier
    verification_tier: Optional[str] = None    # "fast" | "standard" | "deep"


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request with TrustLens extras."""
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    user: Optional[str] = None

    # TrustLens-specific
    trustlens: Optional[TrustLensExtras] = None


class ChatChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: Optional[str] = None


class ChatUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class TrustLensResponseAnnotation(BaseModel):
    """TrustLens trust metadata attached to the response.

    `certificate_id` is the canonical reference; auditors fetch the full cert
    from the certificate store. `certificate_inline` is optional and disabled
    by default to keep responses small.
    """
    certificate_id: str
    certificate_status: str
    pipeline_version: str
    renderable_text_hash: str
    masked_claim_ids: list[str] = Field(default_factory=list)
    degradations: list[str] = Field(default_factory=list)
    certificate: Optional[Certificate] = None


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response with TrustLens annotations."""
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:24]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChatChoice]
    usage: ChatUsage = Field(default_factory=ChatUsage)

    trustlens: Optional[TrustLensResponseAnnotation] = None


class ErrorDetails(BaseModel):
    type: str
    message: str
    code: Optional[str] = None
    retry_after_s: Optional[float] = None


class ErrorResponse(BaseModel):
    error: ErrorDetails

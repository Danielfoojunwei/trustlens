"""Secret redaction for logs and error messages.

LLM provider SDKs sometimes surface the bearer token in stringified errors.
Any user-facing string that could have passed through an SDK exception must
go through ``redact_secrets`` first. This is best-effort pattern matching —
its job is to stop obvious shapes from landing in logs and responses.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Optional


# Provider-style keys. These patterns cover what the major SDKs print when
# they echo credentials back (which unfortunately still happens).
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.=]{8,}", re.IGNORECASE),
    re.compile(r"tlk_[A-Za-z0-9]{8,}"),
]

_DEFAULT_EXTRA_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "TRUSTLENS_BOOTSTRAP_PASSWORD",
)


def redact_secrets(
    text: str,
    *,
    extra: Optional[Iterable[str]] = None,
) -> str:
    """Return ``text`` with well-known secret shapes replaced by ``***``.

    Also redacts any literal values found in selected env vars so that a
    provider key that slipped through the pattern matcher is still masked.
    """
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("***", out)

    env_values: list[str] = []
    for name in _DEFAULT_EXTRA_ENV_VARS:
        v = os.environ.get(name)
        if v and len(v) >= 8:
            env_values.append(v)
    if extra:
        for v in extra:
            if v and len(v) >= 8:
                env_values.append(v)
    for v in env_values:
        if v in out:
            out = out.replace(v, "***")
    return out

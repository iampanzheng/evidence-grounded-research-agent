"""Minimal OpenAI-compatible chat-completions transport (optional remote call)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

from p2_research.application.research.llm import ChatCompletion, LLMError


@dataclass(frozen=True, slots=True)
class OpenAICompatibleChatClient:
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = 30.0
    max_retries: int = 0
    retry_backoff_seconds: float = 2.0
    retry_max_wait_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> OpenAICompatibleChatClient:
        base = os.getenv("P2_LLM_BASE_URL", "https://api.openai.com/v1")
        model = os.getenv("P2_LLM_MODEL", "")
        key = os.getenv("P2_LLM_API_KEY", "")
        if not model or not key:
            raise LLMError("missing_model_or_api_key")
        return cls(base, model, key)

    def complete(self, messages: list[dict[str, str]]) -> ChatCompletion:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise LLMError("invalid_https_base_url")
        if not self.model or not self.api_key or not 0 < self.timeout_seconds <= 120:
            raise LLMError("invalid_provider_configuration")
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = json.dumps({"model": self.model, "messages": messages, "temperature": 0}).encode()
        req = request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "P2-Research-Agent/0.1",
            },
            method="POST",
        )
        if not 0 <= self.max_retries <= 5 or not 0 <= self.retry_backoff_seconds <= 60:
            raise LLMError("invalid_retry_configuration")
        if not 1 <= self.retry_max_wait_seconds <= 120:
            raise LLMError("invalid_retry_configuration")
        for attempt in range(self.max_retries + 1):
            try:
                with request.urlopen(req, timeout=self.timeout_seconds) as response:
                    raw = response.read(1_000_001)
                break
            except error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.max_retries:
                    raise LLMError(f"provider_http_{exc.code}") from exc
                retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
                # Never retry sooner than the provider-specified wait. If that exceeds
                # our single-wait ceiling, fail closed so the batch stops cleanly.
                if retry_after is not None and retry_after > self.retry_max_wait_seconds:
                    raise LLMError(f"provider_http_{exc.code}") from exc
                delay = min(
                    self.retry_max_wait_seconds,
                    max(retry_after or 0.0, self.retry_backoff_seconds * (2**attempt)),
                )
                time.sleep(delay)
            except (error.URLError, TimeoutError, OSError) as exc:
                # Ambiguous transport errors might occur after provider execution.
                # Do not automatically replay potentially billable requests.
                raise LLMError("provider_transport_failed") from exc
        if len(raw) > 1_000_000:
            raise LLMError("provider_response_too_large")
        try:
            body: Any = json.loads(raw)
            content = body["choices"][0]["message"]["content"]
            model = body.get("model", self.model)
            usage = body.get("usage") or {}
            if not isinstance(content, str) or not isinstance(model, str):
                raise TypeError("invalid content")
            if not isinstance(usage, dict):
                usage = {}
            inp = usage.get("prompt_tokens")
            out = usage.get("completion_tokens")
            return ChatCompletion(
                content,
                model,
                inp if isinstance(inp, int) and not isinstance(inp, bool) else None,
                out if isinstance(out, int) and not isinstance(out, bool) else None,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("provider_response_invalid") from exc


def _parse_retry_after(value: str | None) -> float | None:
    """Parse HTTP Retry-After delta-seconds/date; cap happens at caller."""
    if value is None:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if seconds < float("inf") else None
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            return max(0.0, (date - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

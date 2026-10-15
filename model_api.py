"""Client for Meta's Model API (OpenAI-compatible) to call Muse Spark models.

The API key is read from the ``MUSE_SPARK_API_KEY`` environment variable
first, falling back to the ``[model_api]`` config section. An empty key means
model features are disabled; the client raises if used without a key.

Get a key at https://dev.meta.ai (developer console, API keys).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_VAR = "MUSE_SPARK_API_KEY"
DEFAULT_BASE_URL = "https://api.ai.meta.com/v1"
DEFAULT_MODEL = "muse-spark-1.3"


class ModelApiError(Exception):
    """The model call failed or the reply was unusable."""


@dataclass
class ChatResult:
    """One model reply: the text plus the chain-of-thought when available."""

    content: str
    # The model's reasoning/thinking trace, when the API exposes it.
    # Muse Spark is a reasoning model; providers may surface this as
    # ``reasoning_content`` on the message (or similar fields).
    reasoning: str | None = None


class ModelApiClient:
    """Thin wrapper around the OpenAI SDK pointed at Meta's Model API."""

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self.api_key = api_key or os.environ.get(ENV_VAR, "")
        self.model = model
        self.base_url = base_url

    @property
    def enabled(self) -> bool:
        """Whether a key is configured and the client can be used."""
        return bool(self.api_key)

    def _client(self):  # type: ignore[no-untyped-def]
        if not self.enabled:
            raise RuntimeError(
                f"Model API key is not configured (set {ENV_VAR} "
                "or the model_api.api_key config value)"
            )
        from openai import OpenAI

        return OpenAI(api_key=self.api_key, base_url=self.base_url)

    def _completions(self, messages, *, max_tokens, temperature, **kwargs):  # type: ignore[no-untyped-def]
        client = self._client()
        try:
            return client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
        except Exception as exc:
            raise ModelApiError(f"The model call failed: {exc}") from exc

    def chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,  # type: ignore[no-untyped-def]
    ) -> str:
        """Send chat messages, return the assistant's text reply.

        Raises ModelApiError when the call fails or the reply has no content.
        """
        return self.chat_with_reasoning(
            messages, max_tokens=max_tokens, temperature=temperature, **kwargs
        ).content

    def chat_with_reasoning(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,  # type: ignore[no-untyped-def]
    ) -> ChatResult:
        """Send chat messages, return the reply plus the reasoning trace.

        The chain-of-thought is captured when the API exposes it (checked
        defensively on the message and the choice); it is None otherwise.
        Raises ModelApiError when the call fails or the reply has no content.
        """
        response = self._completions(
            messages, max_tokens=max_tokens, temperature=temperature, **kwargs
        )
        choice = response.choices[0] if response.choices else None
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        if not content or not content.strip():
            raise ModelApiError("The model returned an empty reply.")
        reasoning: str | None = None
        for holder in (message, choice):
            if holder is None:
                continue
            for attr in ("reasoning_content", "reasoning"):
                value = getattr(holder, attr, None)
                if isinstance(value, str) and value.strip():
                    reasoning = value
                    break
            if reasoning is not None:
                break
        return ChatResult(content=content, reasoning=reasoning)

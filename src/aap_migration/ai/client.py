"""Minimal client for an OpenAI-compatible ``/v1/chat/completions`` endpoint.

The client never raises into callers: any timeout, HTTP error or invalid model
output ends in ``None`` plus a logged warning, so the AI layer can always be
skipped.  Prompt content and the token are only ever logged at DEBUG level.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class AIConfig(BaseSettings):
    """AI settings, read from ``AI_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="AI_", extra="ignore")

    endpoint: str = ""
    model: str = ""
    token: SecretStr | None = None
    timeout_seconds: float = 30.0
    max_tokens: int = 1024
    temperature: float = 0.1

    @property
    def chat_url(self) -> str:
        base = self.endpoint.strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return f"{base}/v1/chat/completions"


def is_enabled() -> bool:
    """True only when ``AI_ENDPOINT`` is set to a non-empty value."""
    return bool(AIConfig().endpoint.strip())


def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Respond with a single JSON object only, no prose and no code fences. "
        "It must validate against this JSON schema:\n"
        f"{json.dumps(schema.model_json_schema(), sort_keys=True)}"
    )


def _extract_json(content: str) -> str:
    match = _FENCE_RE.match(content)
    return match.group(1) if match else content.strip()


class AIClient:
    """Synchronous chat-completions client returning schema-validated objects."""

    def __init__(
        self,
        config: AIConfig | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or AIConfig()
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.token is not None and self.config.token.get_secret_value():
            headers["Authorization"] = f"Bearer {self.config.token.get_secret_value()}"
        return headers

    def _post(self, messages: list[dict[str, str]]) -> str | None:
        """Send one chat request; return the message content or None on transport errors."""
        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            with httpx.Client(
                timeout=self.config.timeout_seconds, transport=self._transport
            ) as http:
                response = http.post(self.config.chat_url, json=payload, headers=self._headers())
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("ai_request_timeout", timeout_seconds=self.config.timeout_seconds)
            return None
        except httpx.HTTPStatusError as e:
            logger.warning("ai_request_http_error", status_code=e.response.status_code)
            return None
        except httpx.HTTPError as e:
            logger.warning("ai_request_failed", error_type=type(e).__name__)
            return None

        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            # A malformed envelope is treated like invalid model output.
            return ""

    def complete_json(self, system: str, user: str, schema: type[T]) -> T | None:
        """Ask the model for JSON matching ``schema``; retry once on invalid output."""
        if not self.config.endpoint.strip():
            return None

        messages = [
            {"role": "system", "content": f"{system}\n\n{_schema_instruction(schema)}"},
            {"role": "user", "content": user},
        ]
        logger.debug("ai_request", schema=schema.__name__, system=system, user=user)

        for attempt in (1, 2):
            content = self._post(messages)
            if content is None:
                return None
            try:
                return schema.model_validate_json(_extract_json(content))
            except ValidationError as e:
                error = str(e)
            logger.debug("ai_invalid_output", attempt=attempt, content=content, error=error)
            messages = messages + [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "Your previous answer was not valid. Error:\n"
                        f"{error}\nReturn only the corrected JSON object."
                    ),
                },
            ]

        logger.warning("ai_output_invalid_dropped", schema=schema.__name__, attempts=2)
        return None

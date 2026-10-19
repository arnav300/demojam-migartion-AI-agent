"""Minimal client for an OpenAI-compatible ``/v1/chat/completions`` endpoint.

The client never raises into callers: any timeout, HTTP error or invalid model
output ends in ``None`` plus a logged warning, so the AI layer can always be
skipped.  Prompt content and the token are only ever logged at DEBUG level.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from aap_migration.ai.cache import PromptCache, prompt_hash
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
    # One file for every AI answer; a rehearsal fills it, the live demo replays it.
    cache_file: str = ""

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
        cache_path: Path | None = None,
    ) -> None:
        self.config = config or AIConfig()
        self._transport = transport
        path = self.config.cache_file.strip() or cache_path
        self.cache = PromptCache(Path(path)) if path else None

    def with_cache(self, cache_path: Path | None) -> AIClient:
        """Same client with a cache file (``AI_CACHE_FILE`` wins when set)."""
        if cache_path is None or self.cache is not None:
            return self
        return AIClient(self.config, self._transport, cache_path=cache_path)

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

    def complete_json(
        self,
        system: str,
        user: str,
        schema: type[T],
        check: Callable[[T], None] | None = None,
    ) -> T | None:
        """Ask the model for JSON matching ``schema``; retry once on invalid output.

        ``check`` may raise ValueError for an answer that is valid JSON but breaks a
        rule (e.g. a changed RC code); it is treated like a validation error.
        Cached answers are returned without calling the model.
        """
        if not self.config.endpoint.strip():
            return None
        key = prompt_hash(system, user, schema.__name__, self.config.model)
        if self.cache is not None:
            cached = self.cache.get(key, schema)
            if cached is not None and _passes(check, cached):
                logger.debug("ai_cache_hit", schema=schema.__name__)
                return cached

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
                answer = schema.model_validate_json(_extract_json(content))
                if check is not None:
                    check(answer)
                if self.cache is not None:
                    self.cache.put(key, answer)
                return answer
            except (ValidationError, ValueError) as e:
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


def _passes(check: Callable | None, answer: BaseModel) -> bool:
    if check is None:
        return True
    try:
        check(answer)
        return True
    except ValueError:
        return False


def same_rc_code(expected: str) -> Callable[[BaseModel], None]:
    """Check for answers that must keep the rule-decided RC code."""

    def check(answer: BaseModel) -> None:
        got = getattr(answer, "rc_code", None)
        if got != expected:
            raise ValueError(f"rc_code must be {expected!r} exactly (got {got!r}); do not change it")

    return check

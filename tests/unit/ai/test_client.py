"""Unit tests for the AI client (model stubbed with httpx.MockTransport)."""

import json

import httpx
import pytest
import structlog
from aap_migration.ai.client import AIClient, AIConfig, is_enabled
from pydantic import BaseModel

pytestmark = pytest.mark.unit


class _Answer(BaseModel):
    answer: str
    score: int


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _client(handler, token: str | None = "test-token") -> AIClient:
    config = AIConfig(endpoint="http://ai.test", model="granite", token=token)
    return AIClient(config=config, transport=httpx.MockTransport(handler))


def test_disabled_when_endpoint_unset(monkeypatch):
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    assert is_enabled() is False
    monkeypatch.setenv("AI_ENDPOINT", "  ")
    assert is_enabled() is False
    monkeypatch.setenv("AI_ENDPOINT", "http://ai.test")
    assert is_enabled() is True


def test_config_defaults_from_env(monkeypatch):
    for var in ("AI_TIMEOUT_SECONDS", "AI_MAX_TOKENS", "AI_TEMPERATURE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AI_ENDPOINT", "http://ai.test/v1/")
    config = AIConfig()
    assert config.timeout_seconds == 30
    assert config.max_tokens == 1024
    assert config.temperature == 0.1
    assert config.chat_url == "http://ai.test/v1/chat/completions"


def test_complete_json_disabled_makes_no_call():
    def handler(request):
        raise AssertionError("no request expected")

    client = AIClient(config=AIConfig(endpoint=""), transport=httpx.MockTransport(handler))
    assert client.complete_json("sys", "user", _Answer) is None


def test_valid_json():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat('{"answer": "ok", "score": 3}')

    result = _client(handler).complete_json("sys", "user", _Answer)

    assert result == _Answer(answer="ok", score=3)
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "http://ai.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-token"
    body = json.loads(request.content)
    assert body["model"] == "granite"
    assert body["response_format"] == {"type": "json_object"}
    assert '"score"' in body["messages"][0]["content"]  # schema stated in system prompt
    assert body["messages"][1] == {"role": "user", "content": "user"}


def test_no_auth_header_without_token():
    seen = []

    def handler(request):
        seen.append(request)
        return _chat('{"answer": "ok", "score": 1}')

    assert _client(handler, token=None).complete_json("s", "u", _Answer) is not None
    assert "Authorization" not in seen[0].headers


def test_code_fenced_json_is_accepted():
    result = _client(lambda r: _chat('```json\n{"answer": "x", "score": 1}\n```')).complete_json(
        "s", "u", _Answer
    )
    assert result == _Answer(answer="x", score=1)


def test_invalid_then_valid_retries_once_with_error():
    replies = iter(['{"answer": "x"}', '{"answer": "x", "score": 2}'])
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _chat(next(replies))

    result = _client(handler).complete_json("s", "u", _Answer)

    assert result == _Answer(answer="x", score=2)
    assert len(bodies) == 2
    retry_messages = bodies[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": '{"answer": "x"}'}
    assert "score" in retry_messages[-1]["content"]


def test_invalid_twice_returns_none():
    calls = []

    def handler(request):
        calls.append(request)
        return _chat("not json at all")

    assert _client(handler).complete_json("s", "u", _Answer) is None
    assert len(calls) == 2


def test_timeout_returns_none():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    assert _client(handler).complete_json("s", "u", _Answer) is None


def test_http_500_returns_none():
    assert _client(lambda r: httpx.Response(500, text="boom")).complete_json(
        "s", "u", _Answer
    ) is None


def test_malformed_envelope_counts_as_invalid():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"unexpected": True})

    assert _client(handler).complete_json("s", "u", _Answer) is None
    assert len(calls) == 2


@pytest.mark.parametrize(
    "handler",
    [lambda r: httpx.Response(500), lambda r: _chat("not json"), lambda r: _chat('{"a": 1}')],
)
def test_token_and_prompt_never_logged_above_debug(handler):
    with structlog.testing.capture_logs() as logs:
        _client(handler).complete_json("secret prompt", "secret user", _Answer)

    above_debug = [entry for entry in logs if entry["log_level"] != "debug"]
    assert above_debug, "expected a warning"
    for entry in above_debug:
        text = repr(entry)
        assert "test-token" not in text
        assert "secret prompt" not in text
        assert "secret user" not in text
        assert "not json" not in text


class _Rc(BaseModel):
    rc_code: str


def test_cache_replays_without_calling_model(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return _chat('{"answer": "ok", "score": 1}')

    cache = tmp_path / "ai_cache.json"
    first = AIClient(AIConfig(endpoint="http://ai.test", model="granite"), httpx.MockTransport(handler), cache_path=cache)
    assert first.complete_json("s", "u", _Answer) == _Answer(answer="ok", score=1)
    assert cache.is_file()

    replay = AIClient(AIConfig(endpoint="http://ai.test", model="granite"), httpx.MockTransport(handler), cache_path=cache)
    assert replay.complete_json("s", "u", _Answer) == _Answer(answer="ok", score=1)
    assert replay.complete_json("s", "other prompt", _Answer) is not None
    assert len(calls) == 2  # only the new prompt reached the model


def test_cache_key_includes_model(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return _chat('{"answer": "ok", "score": 1}')

    cache = tmp_path / "c.json"
    for model in ("granite-8b", "granite-2b"):
        AIClient(AIConfig(endpoint="http://ai.test", model=model), httpx.MockTransport(handler), cache_path=cache).complete_json("s", "u", _Answer)
    assert len(calls) == 2


def test_ai_cache_file_env_wins(tmp_path, monkeypatch):
    shared = tmp_path / "shared.json"
    monkeypatch.setenv("AI_CACHE_FILE", str(shared))
    client = AIClient(AIConfig(endpoint="http://ai.test"), httpx.MockTransport(lambda r: _chat('{"answer": "a", "score": 1}')))
    client = client.with_cache(tmp_path / "per-report.json")
    client.complete_json("s", "u", _Answer)
    assert shared.is_file() and not (tmp_path / "per-report.json").exists()


def test_check_failure_retries_with_reason_and_is_not_cached(tmp_path):
    from aap_migration.ai.client import same_rc_code

    replies = iter(['{"rc_code": "RC-005"}', '{"rc_code": "RC-002"}'])
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _chat(next(replies))

    cache = tmp_path / "c.json"
    client = AIClient(AIConfig(endpoint="http://ai.test"), httpx.MockTransport(handler), cache_path=cache)
    assert client.complete_json("s", "u", _Rc, check=same_rc_code("RC-002")) == _Rc(rc_code="RC-002")
    assert "rc_code must be 'RC-002'" in bodies[1]["messages"][-1]["content"]
    assert list(json.loads(cache.read_text()).values()) == [{"rc_code": "RC-002"}]


def test_check_failing_twice_returns_none(tmp_path):
    from aap_migration.ai.client import same_rc_code

    client = AIClient(AIConfig(endpoint="http://ai.test"), httpx.MockTransport(lambda r: _chat('{"rc_code": "RC-005"}')), cache_path=tmp_path / "c.json")
    assert client.complete_json("s", "u", _Rc, check=same_rc_code("RC-002")) is None
    assert not (tmp_path / "c.json").exists()

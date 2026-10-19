"""Shared fixtures for AI-layer unit tests."""

import pytest
import structlog


@pytest.fixture(autouse=True)
def _no_shared_ai_cache(monkeypatch):
    """A developer's AI_CACHE_FILE must never leak into tests."""
    monkeypatch.delenv("AI_CACHE_FILE", raising=False)


@pytest.fixture(autouse=True)
def _reset_structlog():
    """CLI tests call configure_logging(); keep structlog state from leaking between tests."""
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()

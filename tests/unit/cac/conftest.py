"""Shared fixtures for AI-layer unit tests."""

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_structlog():
    """CLI tests call configure_logging(); keep structlog state from leaking between tests."""
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()

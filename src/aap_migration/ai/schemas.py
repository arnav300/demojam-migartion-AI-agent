"""Pydantic schemas every model response is parsed into.

Raw model text is never rendered; only instances of these models are.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Confidence = Literal["high", "medium", "low"]

RC_CODES = tuple(f"RC-{n:03d}" for n in range(1, 12))
RC_CODE_PATTERN = r"^RC-0(0[1-9]|1[01])$"

_SENTENCE_END_RE = re.compile(r"[.!?]+(?:\s+|$)")


def _count_sentences(text: str) -> int:
    parts = [p for p in _SENTENCE_END_RE.split(text.strip()) if p.strip()]
    return len(parts)


def _max_words(limit: int):
    def check(value: str) -> str:
        if len(value.split()) > limit:
            raise ValueError(f"must be at most {limit} words")
        return value

    return check


NonEmpty = Field(min_length=1)


class FindingExplanation(BaseModel):
    """Plain-English explanation of one pre-migration root cause (C7)."""

    # STALE is the analyzer's cleanup-candidate category next to RC-001…RC-011.
    rc_code: str = Field(pattern=r"^(RC-0(0[1-9]|1[01])|STALE)$")
    summary: str = NonEmpty
    why_it_fails: str = NonEmpty
    operator_action: str = NonEmpty
    confidence: Confidence

    @field_validator("summary")
    @classmethod
    def _at_most_two_sentences(cls, value: str) -> str:
        if _count_sentences(value) > 2:
            raise ValueError("summary must be at most 2 sentences")
        return value


class FailureClassification(BaseModel):
    """Explanation of one post-migration failure group (C8)."""

    rc_code: str = Field(pattern=r"^(RC-0(0[1-9]|1[01])|UNCLASSIFIED)$")
    explanation: str = NonEmpty
    confidence: Confidence


class RemediationPlaybook(BaseModel):
    """Draft remediation playbook for one root cause (C10)."""

    rc_code: str = Field(pattern=RC_CODE_PATTERN)
    title: str = NonEmpty
    playbook_yaml: str = NonEmpty
    assumptions: list[str]


class ReportNarrative(BaseModel):
    """Executive narrative wrapped around our own numbers (C9)."""

    headline: str = NonEmpty
    what_migrated: str = NonEmpty
    what_failed: str = NonEmpty
    what_to_do_next: str = NonEmpty

    _limit_words = field_validator(
        "headline", "what_migrated", "what_failed", "what_to_do_next"
    )(_max_words(120))

"""Unit tests for AI response schemas and prompt builders."""

import json

import pytest
from pydantic import ValidationError

from aap_migration.ai import prompts
from aap_migration.ai.schemas import (
    FailureClassification,
    FindingExplanation,
    RemediationPlaybook,
    ReportNarrative,
)

pytestmark = pytest.mark.unit

VALID = {
    FindingExplanation: {
        "rc_code": "RC-001",
        "summary": "The project cannot sync. Import will be rejected.",
        "why_it_fails": "AAP 2.6 validates the playbook at creation.",
        "operator_action": "Fix the SCM URL and sync on 2.4.",
        "confidence": "high",
    },
    FailureClassification: {
        "rc_code": "UNCLASSIFIED",
        "explanation": "Unknown API error.",
        "confidence": "low",
    },
    RemediationPlaybook: {
        "rc_code": "RC-011",
        "title": "Map users",
        "playbook_yaml": "- hosts: localhost\n  tasks: []\n",
        "assumptions": [],
    },
    ReportNarrative: {
        "headline": "Most objects migrated.",
        "what_migrated": "Organizations and projects.",
        "what_failed": "Some job templates.",
        "what_to_do_next": "Fix the projects and retry.",
    },
}


@pytest.mark.parametrize("schema", list(VALID))
def test_valid_payload_parses(schema):
    assert schema.model_validate(VALID[schema])


@pytest.mark.parametrize(
    ("schema", "field"),
    [(schema, field) for schema, payload in VALID.items() for field in payload],
)
def test_missing_field_rejected(schema, field):
    payload = {k: v for k, v in VALID[schema].items() if k != field}
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


@pytest.mark.parametrize(
    ("schema", "field", "value"),
    [
        (FindingExplanation, "rc_code", "RC-012"),
        (FindingExplanation, "rc_code", "UNCLASSIFIED"),
        (FindingExplanation, "confidence", "very high"),
        (FindingExplanation, "summary", "One. Two. Three."),
        (FindingExplanation, "summary", ""),
        (FailureClassification, "rc_code", "RC-000"),
        (FailureClassification, "rc_code", "STALE"),
        (FailureClassification, "confidence", "certain"),
        (RemediationPlaybook, "rc_code", "UNCLASSIFIED"),
        (RemediationPlaybook, "playbook_yaml", ""),
        (RemediationPlaybook, "assumptions", "not a list"),
        (ReportNarrative, "headline", "word " * 121),
        (ReportNarrative, "what_to_do_next", ""),
    ],
)
def test_out_of_range_rejected(schema, field, value):
    payload = {**VALID[schema], field: value}
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_boundaries_accepted():
    FindingExplanation.model_validate({**VALID[FindingExplanation], "rc_code": "STALE"})
    FailureClassification.model_validate({**VALID[FailureClassification], "rc_code": "RC-010"})
    ReportNarrative.model_validate({**VALID[ReportNarrative], "headline": "word " * 120})


def _facts(user: str) -> dict:
    assert user.startswith("Facts (JSON):\n")
    return json.loads(user.split("\n", 1)[1])


def _assert_system_rules(system: str):
    assert "never change it" in system
    assert "Answer only from the facts supplied" in system
    assert "Do not invent resource names, numbers" in system
    assert "JSON only" in system


BUILDERS = [
    lambda names: prompts.build_explanation_prompt("RC-002", "job_templates", names, len(names)),
    lambda names: prompts.build_classification_prompt(
        "RC-002", "API: Inventory required", "job_templates", names, len(names)
    ),
    lambda names: prompts.build_remediation_prompt("RC-002", "API: Inventory required", names),
]


@pytest.mark.parametrize("build", BUILDERS)
def test_builders_include_rc_and_only_given_facts(build):
    names = ["jt-a", "jt-b", "jt-c", "jt-d", "jt-e", "jt-f"]
    system, user = build(names)

    _assert_system_rules(system)
    facts = _facts(user)
    assert facts["rc_code"] == "RC-002"
    assert facts["rc_title"] == "Job template without inventory"
    assert facts["sample_resource_names"] == names[:5]
    assert "jt-f" not in user  # capped at 5 samples

    _, other_user = build(["other-org-jt"])
    assert "jt-a" not in other_user
    assert "other-org-jt" not in user


def test_narrative_prompt_contains_only_aggregates():
    aggregates = {"totals": {"failed": 3, "completed": 10}, "per_rc": {"RC-001": 3}}
    system, user = prompts.build_narrative_prompt(aggregates)

    _assert_system_rules(system)
    assert _facts(user) == {"aggregates": aggregates}

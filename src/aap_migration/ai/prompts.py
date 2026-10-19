"""Prompt builders, one per response schema.  Each returns ``(system, user)``.

Builders only see the facts passed to them, so a prompt can never contain data
from other resources.  Callers pass names and counts only, never secrets.
"""

from __future__ import annotations

import json
from typing import Any

from aap_migration.analysis.premigration import RC_CATALOG

MAX_SAMPLES = 5

_RULES = (
    "Rules:\n"
    "- Root-cause codes (rc_code) are decided by deterministic rules and are given "
    "in the facts. Copy any rc_code unchanged into your answer; never change it.\n"
    "- Answer only from the facts supplied. Do not invent resource names, numbers, "
    "versions or settings that are not in the facts.\n"
    "- Respond with JSON only."
)

_ROLE = (
    "You are an assistant for an Ansible Automation Platform 2.4 to 2.6 migration. "
    "You write for operators who must act on the result."
)


def _system(task: str) -> str:
    return f"{_ROLE}\n{task}\n\n{_RULES}"


def _user(facts: dict[str, Any]) -> str:
    return "Facts (JSON):\n" + json.dumps(facts, indent=2, sort_keys=True, default=str)


def _rc_facts(rc_code: str) -> dict[str, str]:
    info = RC_CATALOG.get(rc_code, {})
    return {
        "rc_code": rc_code,
        "rc_title": info.get("title", ""),
        "rc_description": info.get("description", ""),
        "rc_remediation": info.get("remediation", ""),
    }


def _samples(names: list[str]) -> list[str]:
    return [str(n) for n in names[:MAX_SAMPLES]]


def build_explanation_prompt(
    rc_code: str, resource_type: str, sample_names: list[str], count: int
) -> tuple[str, str]:
    """Prompt for a :class:`FindingExplanation` of one (rc_code, resource_type) group."""
    system = _system(
        "Explain in plain English why these resources are predicted to have this "
        "problem when migrated, and what the operator should do before migrating. "
        "The summary is at most 2 sentences."
    )
    facts = {
        **_rc_facts(rc_code),
        "resource_type": resource_type,
        "affected_count": count,
        "sample_resource_names": _samples(sample_names),
    }
    return system, _user(facts)


def build_classification_prompt(
    rc_code: str, error_key: str, resource_type: str, sample_names: list[str], count: int
) -> tuple[str, str]:
    """Prompt for a :class:`FailureClassification` of one (rc_code, error_key) group."""
    system = _system(
        "A migration import failed with the normalised error below. Explain in plain "
        "English what the error means for these resources and rate your confidence "
        "that the given rc_code describes it."
    )
    facts = {
        **_rc_facts(rc_code),
        "error_key": error_key,
        "resource_type": resource_type,
        "failed_count": count,
        "sample_resource_names": _samples(sample_names),
    }
    return system, _user(facts)


def build_remediation_prompt(
    rc_code: str, error_key: str, sample_names: list[str]
) -> tuple[str, str]:
    """Prompt for a :class:`RemediationPlaybook` for one RC code."""
    system = _system(
        "Draft an Ansible playbook that helps an operator fix this root cause on the "
        "AAP 2.6 target. Use only the ansible.platform and ansible.controller "
        "collections. Run against localhost with connection: local, and set "
        "check_mode from a variable that defaults to true. Take resource names from "
        "a list variable; never hard-code hostnames, URLs or credentials. "
        "List every assumption you make."
    )
    facts = {
        **_rc_facts(rc_code),
        "error_key": error_key,
        "sample_resource_names": _samples(sample_names),
    }
    return system, _user(facts)


def build_narrative_prompt(aggregates: dict[str, Any]) -> tuple[str, str]:
    """Prompt for a :class:`ReportNarrative` from aggregate counts only."""
    system = _system(
        "Write a short executive summary of this migration for a non-technical "
        "reader. Each field is at most 120 words. Do not write any number or digit "
        "(AAP versions and RC codes are allowed); the report shows our numbers next "
        "to your sentences."
    )
    return system, _user({"aggregates": aggregates})

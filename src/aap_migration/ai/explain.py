"""C7: plain-English explanations for pre-migration findings.

Findings are grouped by ``(rc_code, resource_type)`` and each group costs at
most one model call (plus one retry).  Answers are cached in ``ai_cache.json``
next to the report (or ``AI_CACHE_FILE``), keyed by a hash of the prompt, so
re-running the report does not call the model again.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from aap_migration.ai.client import AIClient, is_enabled, same_rc_code
from aap_migration.ai.prompts import MAX_SAMPLES, build_explanation_prompt
from aap_migration.ai.schemas import FindingExplanation
from aap_migration.analysis.premigration import Finding, PreMigrationResult
from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

CACHE_FILENAME = "ai_cache.json"


def group_findings(findings: list[Finding]) -> dict[tuple[str, str], list[Finding]]:
    """Group findings by ``(rc_code, resource_type)``."""
    groups: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for finding in findings:
        groups[(finding.rc, finding.resource_type)].append(finding)
    return dict(sorted(groups.items()))


def _sample_names(findings: list[Finding]) -> list[str]:
    """Up to MAX_SAMPLES distinct resource names, sorted so prompts are stable."""
    return sorted({str(f.resource_name) for f in findings})[:MAX_SAMPLES]


def explain_groups(
    result: PreMigrationResult,
    client: AIClient | None = None,
    cache_path: Path | None = None,
) -> dict[tuple[str, str], FindingExplanation]:
    """One explanation per ``(rc_code, resource_type)`` group (cache first, then model)."""
    if client is None:
        if not is_enabled():
            return {}
        client = AIClient()
    client = client.with_cache(cache_path)

    explanations: dict[tuple[str, str], FindingExplanation] = {}
    for (rc_code, resource_type), findings in group_findings(result.findings).items():
        system, user = build_explanation_prompt(
            rc_code, resource_type, _sample_names(findings), len(findings)
        )
        # Rules decide the RC code; an answer that changes it is retried once, then dropped.
        explanation = client.complete_json(
            system, user, FindingExplanation, check=same_rc_code(rc_code)
        )
        if explanation is not None:
            explanations[(rc_code, resource_type)] = explanation
    return explanations


def explain_findings(
    result: PreMigrationResult,
    client: AIClient | None = None,
    cache_path: Path | None = None,
) -> dict[str, FindingExplanation]:
    """Explanations keyed by RC code.

    Each RC code gets the explanation of its largest ``(rc_code, resource_type)``
    group.  Returns an empty dict when AI is off or every call failed.
    """
    groups = group_findings(result.findings)
    by_group = explain_groups(result, client=client, cache_path=cache_path)
    # Largest group first; ties go to the alphabetically first resource type.
    ranked = sorted(by_group, key=lambda g: (g[0], -len(groups[g]), g[1]))
    explanations: dict[str, FindingExplanation] = {}
    for rc_code, resource_type in ranked:
        explanations.setdefault(rc_code, by_group[(rc_code, resource_type)])
    return explanations

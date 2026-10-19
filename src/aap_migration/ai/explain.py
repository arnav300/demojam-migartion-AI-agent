"""C7: plain-English explanations for pre-migration findings.

Findings are grouped by ``(rc_code, resource_type)`` and each group costs at
most one model call.  Results are cached in ``ai_cache.json`` keyed by a hash
of the prompt, so re-running the report does not call the model again.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from pydantic import ValidationError

from aap_migration.ai.client import AIClient, is_enabled
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


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n\x00\n{user}".encode()).hexdigest()


class PromptCache:
    """JSON file mapping prompt hash -> validated model output."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._data: dict[str, dict] = {}
        if path and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
            except (OSError, json.JSONDecodeError):
                logger.warning("ai_cache_unreadable", path=str(path))

    def get(self, key: str) -> FindingExplanation | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        try:
            return FindingExplanation.model_validate(entry)
        except ValidationError:
            return None

    def put(self, key: str, value: FindingExplanation) -> None:
        self._data[key] = value.model_dump()

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1, sort_keys=True), encoding="utf-8")


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

    cache = PromptCache(cache_path)
    explanations: dict[tuple[str, str], FindingExplanation] = {}
    for (rc_code, resource_type), findings in group_findings(result.findings).items():
        system, user = build_explanation_prompt(
            rc_code, resource_type, _sample_names(findings), len(findings)
        )
        key = prompt_hash(system, user)
        explanation = cache.get(key)
        if explanation is None:
            explanation = client.complete_json(system, user, FindingExplanation)
            if explanation is None:
                continue
            if explanation.rc_code != rc_code:
                # Rules decide the RC code; an answer that changes it is dropped.
                logger.warning("ai_rc_code_changed", expected=rc_code, got=explanation.rc_code)
                continue
            cache.put(key, explanation)
        explanations[(rc_code, resource_type)] = explanation

    cache.save()
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

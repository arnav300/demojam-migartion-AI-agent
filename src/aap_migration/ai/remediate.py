"""C10: remediation playbooks per RC code.

For every RC code in ``classification.json`` the model may draft a
:class:`RemediationPlaybook`.  A draft is only written when it passes our
static checks, ``ansible-playbook --syntax-check`` and ``ansible-lint`` (lint is
skipped with a warning when not installed).  Otherwise, and when AI is off, the
hand-written template ``<templates>/RC-0XX.yml`` is copied instead.  Nothing is
ever run against an AAP instance.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from aap_migration.ai.client import AIClient, is_enabled, same_rc_code
from aap_migration.ai.prompts import MAX_SAMPLES, build_remediation_prompt
from aap_migration.ai.schemas import RC_CODES, RemediationPlaybook
from aap_migration.analysis.premigration import RC_CATALOG
from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

ALLOWED_MODULE_PREFIXES = ("ansible.platform.", "ansible.controller.", "ansible.builtin.")
CHECK_MODE_VAR = "remediation_check_mode"
TARGETS_VAR = "remediation_targets"
CHECK_TIMEOUT_SECONDS = 120

# Task keywords that are not module names.
_TASK_KEYWORDS = {
    "name", "when", "loop", "loop_control", "register", "vars", "tags", "check_mode",
    "diff", "ignore_errors", "changed_when", "failed_when", "no_log", "delegate_to",
    "run_once", "until", "retries", "delay", "environment", "become", "notify",
    "with_items", "with_list", "module_defaults", "args", "any_errors_fatal",
}

Runner = Callable[[list[str], Path], subprocess.CompletedProcess]


@dataclass
class RCInput:
    rc_code: str
    error_key: str
    sample_names: list[str]
    count: int


@dataclass
class RemediationEntry:
    rc_code: str
    source: str  # generated | template | missing
    playbook: str | None = None
    vars_file: str | None = None
    reason: str = ""
    assumptions: list[str] = field(default_factory=list)


def load_rc_inputs(classification: dict[str, Any]) -> list[RCInput]:
    """One input per RC code present: its most frequent error key and up to 5 names."""
    by_rc: dict[str, list[dict]] = {}
    for group in classification.get("groups", []):
        if group.get("rc_code") in RC_CODES:
            by_rc.setdefault(group["rc_code"], []).append(group)
    inputs = []
    for rc_code, groups in sorted(by_rc.items()):
        top = max(groups, key=lambda g: (g.get("count", 0), g.get("error_key", "")))
        names = sorted({n for g in groups for n in g.get("sample_names", [])})[:MAX_SAMPLES]
        inputs.append(
            RCInput(rc_code, top.get("error_key", ""), names, sum(g.get("count", 0) for g in groups))
        )
    return inputs


def static_problems(playbook_yaml: str) -> list[str]:
    """Our own checks: localhost only, AAP 2.6 collections only, check_mode from the var."""
    try:
        plays = yaml.safe_load(playbook_yaml)
    except yaml.YAMLError as e:
        return [f"invalid YAML: {e}"]
    if not isinstance(plays, list) or not plays:
        return ["playbook must be a non-empty list of plays"]
    problems = []
    for play in plays:
        if not isinstance(play, dict):
            problems.append("every play must be a mapping")
            continue
        if play.get("hosts") != "localhost":
            problems.append("plays must target localhost")
        if CHECK_MODE_VAR not in str(play.get("check_mode", "")):
            problems.append(f"plays must set check_mode from {CHECK_MODE_VAR}")
        for section in ("pre_tasks", "tasks", "post_tasks", "handlers"):
            for task in play.get(section) or []:
                problems.extend(_task_problems(task))
    return problems


def _task_problems(task: Any) -> list[str]:
    if not isinstance(task, dict):
        return ["every task must be a mapping"]
    if "block" in task:
        return [p for key in ("block", "rescue", "always") for t in task.get(key) or [] for p in _task_problems(t)]
    modules = [k for k in task if k not in _TASK_KEYWORDS]
    return [
        f"module '{m}' is not from ansible.platform / ansible.controller / ansible.builtin"
        for m in modules
        if not m.startswith(ALLOWED_MODULE_PREFIXES)
    ]


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=CHECK_TIMEOUT_SECONDS, check=False
    )


def tool_checks(
    playbook: Path, vars_file: Path, runner: Runner = _run, which: Callable = shutil.which
) -> list[str]:
    """``ansible-playbook --syntax-check`` (required) and ``ansible-lint`` (optional)."""
    if not which("ansible-playbook"):
        return ["ansible-playbook is not installed, so the draft cannot be syntax-checked"]
    cwd = playbook.parent
    syntax = runner(
        ["ansible-playbook", "--syntax-check", "-i", "localhost,", "-e", f"@{vars_file.name}", playbook.name],
        cwd,
    )
    if syntax.returncode != 0:
        return [f"syntax-check failed: {(syntax.stderr or syntax.stdout).strip()[:300]}"]
    if not which("ansible-lint"):
        logger.warning("ansible_lint_not_installed", playbook=playbook.name)
        return []
    lint = runner(["ansible-lint", "--offline", "--nocolor", playbook.name], cwd)
    if lint.returncode != 0:
        return [f"ansible-lint failed: {(lint.stdout or lint.stderr).strip()[:300]}"]
    return []


def vars_yaml(rc: RCInput) -> str:
    data = {
        CHECK_MODE_VAR: True,
        TARGETS_VAR: rc.sample_names,
    }
    header = (
        f"# Variables for {rc.rc_code}. check mode is on by default: set "
        f"{CHECK_MODE_VAR}: false only after reviewing a check-mode run.\n"
        "# Target AAP 2.6 connection comes from the environment (see README.md), never this file.\n"
    )
    return header + yaml.safe_dump(data, sort_keys=False)


def _draft(client: AIClient, rc: RCInput) -> RemediationPlaybook | None:
    system, user = build_remediation_prompt(rc.rc_code, rc.error_key, rc.sample_names)
    system += (
        f"\nThe play must set `check_mode: \"{{{{ {CHECK_MODE_VAR} | default(true) }}}}\"` and "
        f"take resource names from the list variable `{TARGETS_VAR}`."
    )
    return client.complete_json(system, user, RemediationPlaybook, check=same_rc_code(rc.rc_code))


def build_remediation(
    classification: dict[str, Any],
    out_dir: Path,
    templates_dir: Path,
    client: AIClient | None = None,
    use_ai: bool | None = None,
    runner: Runner = _run,
    which: Callable = shutil.which,
) -> list[RemediationEntry]:
    """Write one playbook per RC code into ``out_dir`` plus ``README.md``."""
    available = client is not None or is_enabled()
    ai_on = available if use_ai is None else (use_ai and available)
    if ai_on and client is None:
        client = AIClient()

    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for rc in load_rc_inputs(classification):
        entry = None
        if ai_on:
            entry = _try_generated(client, rc, out_dir, runner, which)
        if entry is None or entry.source != "generated":
            reason = entry.reason if entry else "AI off"
            entry = _copy_template(rc, out_dir, templates_dir, reason)
        entries.append(entry)

    (out_dir / "README.md").write_text(render_index(entries), encoding="utf-8")
    return entries


def _try_generated(
    client: AIClient, rc: RCInput, out_dir: Path, runner: Runner, which: Callable
) -> RemediationEntry:
    draft = _draft(client, rc)
    if draft is None:
        return RemediationEntry(rc.rc_code, "template", reason="no valid AI draft")
    problems = static_problems(draft.playbook_yaml)
    if not problems:
        with tempfile.TemporaryDirectory(prefix="remediate-") as tmp:
            playbook = Path(tmp) / f"{rc.rc_code}.yml"
            vars_file = Path(tmp) / f"{rc.rc_code}.vars.yml"
            playbook.write_text(draft.playbook_yaml, encoding="utf-8")
            vars_file.write_text(vars_yaml(rc), encoding="utf-8")
            problems = tool_checks(playbook, vars_file, runner=runner, which=which)
    if problems:
        logger.warning("ai_playbook_rejected", rc_code=rc.rc_code, problems=problems)
        return RemediationEntry(rc.rc_code, "template", reason="; ".join(problems))

    header = (
        f"# {rc.rc_code}: {draft.title}\n"
        "# Drafted by AI and validated (static checks, syntax-check, ansible-lint).\n"
        "# Review before running. Runs in check mode unless the vars file says otherwise.\n"
    )
    (out_dir / f"{rc.rc_code}.yml").write_text(header + draft.playbook_yaml, encoding="utf-8")
    (out_dir / f"{rc.rc_code}.vars.yml").write_text(vars_yaml(rc), encoding="utf-8")
    return RemediationEntry(
        rc.rc_code,
        "generated",
        playbook=f"{rc.rc_code}.yml",
        vars_file=f"{rc.rc_code}.vars.yml",
        assumptions=draft.assumptions,
    )


def _copy_template(rc: RCInput, out_dir: Path, templates_dir: Path, reason: str) -> RemediationEntry:
    template = templates_dir / f"{rc.rc_code}.yml"
    if not template.is_file():
        logger.warning("remediation_template_missing", rc_code=rc.rc_code, path=str(template))
        return RemediationEntry(rc.rc_code, "missing", reason=f"{reason}; no template {template}")
    shutil.copyfile(template, out_dir / f"{rc.rc_code}.yml")
    (out_dir / f"{rc.rc_code}.vars.yml").write_text(vars_yaml(rc), encoding="utf-8")
    return RemediationEntry(
        rc.rc_code,
        "template",
        playbook=f"{rc.rc_code}.yml",
        vars_file=f"{rc.rc_code}.vars.yml",
        reason=reason,
    )


def render_index(entries: list[RemediationEntry]) -> str:
    lines = [
        "# Remediation playbooks",
        "",
        "One playbook per root cause found by `aap-migrate classify-failures`. They target the",
        "AAP 2.6 platform only (`ansible.platform` / `ansible.controller`) and run in **check mode**",
        f"by default (`{CHECK_MODE_VAR}: true` in each vars file). Nothing here was run.",
        "",
        "| RC code | Root cause | Source | Playbook | Notes |",
        "|---|---|---|---|---|",
    ]
    for e in entries:
        title = RC_CATALOG.get(e.rc_code, {}).get("title", "")
        playbook = f"`{e.playbook}`" if e.playbook else "—"
        notes = e.reason if e.source != "generated" else "; ".join(e.assumptions)
        lines.append(f"| {e.rc_code} | {title} | {e.source} | {playbook} | {notes.replace('|', '/')} |")
    lines += [
        "",
        "## How to run",
        "",
        "Point the collections at the AAP 2.6 target through the environment (never in files):",
        "",
        "```bash",
        "export CONTROLLER_HOST=https://<aap26-host> CONTROLLER_OAUTH_TOKEN=<token>",
        "export AAP_HOSTNAME=$CONTROLLER_HOST AAP_TOKEN=$CONTROLLER_OAUTH_TOKEN",
        "ansible-playbook RC-0XX.yml -e @RC-0XX.vars.yml            # check mode (default)",
        f"ansible-playbook RC-0XX.yml -e @RC-0XX.vars.yml -e '{{\"{CHECK_MODE_VAR}\": false}}'   # apply",
        "```",
        "",
        "`source` is `generated` (AI draft that passed validation), `template` (hand-written",
        "template from `remediation/templates/`) or `missing` (no valid draft and no template).",
        "",
    ]
    return "\n".join(lines)


def load_classification(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_counts(entries: list[RemediationEntry]) -> dict[str, int]:
    return dict(Counter(e.source for e in entries))

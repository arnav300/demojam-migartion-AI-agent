"""``remediate``: one remediation playbook per RC code (C10).

Reads ``classification.json`` from ``classify-failures``. With AI on, Granite
drafts a playbook that must pass static checks, ``ansible-playbook
--syntax-check`` and ``ansible-lint``; otherwise the template from
``remediation/templates/RC-0XX.yml`` is copied. Nothing is run against AAP.
"""

from __future__ import annotations

from pathlib import Path

import click

from aap_migration.ai.remediate import build_remediation, load_classification, source_counts
from aap_migration.cli.commands.premigration_report import resolve_ai
from aap_migration.cli.decorators import handle_errors
from aap_migration.cli.utils import echo_info, echo_success, echo_warning


@click.command(name="remediate")
@click.option(
    "--from",
    "classification_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="classification.json written by 'classify-failures'.",
)
@click.option(
    "--out",
    "out_dir",
    default="remediation",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for the playbooks and README.md.",
)
@click.option(
    "--templates",
    "templates_dir",
    default="remediation/templates",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Hand-written fallback templates (RC-0XX.yml).",
)
@click.option(
    "--ai/--no-ai",
    "use_ai",
    default=None,
    help="Let the AI draft playbooks (default: on when AI_ENDPOINT is set).",
)
@handle_errors
def remediate(
    classification_file: Path, out_dir: Path, templates_dir: Path, use_ai: bool | None
) -> None:
    """Write one remediation playbook per RC code, in check mode by default.

    \b
    Examples:
        aap-migrate remediate --from reports/classification.json --out remediation/
        aap-migrate remediate --from reports/classification.json --no-ai
    """
    ai_on = resolve_ai(use_ai)
    echo_info(f"Building remediation playbooks (AI {'on' if ai_on else 'off'}) ...")
    entries = build_remediation(
        load_classification(classification_file), out_dir, templates_dir, use_ai=ai_on
    )
    for entry in entries:
        line = f"{entry.rc_code}: {entry.source}" + (f" -> {out_dir / entry.playbook}" if entry.playbook else "")
        (echo_warning if entry.source == "missing" else echo_info)(line)
    echo_success(f"Written {out_dir / 'README.md'} {source_counts(entries)}")

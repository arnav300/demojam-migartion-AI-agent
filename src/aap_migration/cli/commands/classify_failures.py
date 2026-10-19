"""``classify-failures``: root-cause classification of failed resources (C8).

Reads ``migration_progress`` from the state database (read-only), assigns each
failure an RC code with deterministic rules and, when AI is on, adds a short
explanation per ``(rc_code, error_key)`` group.

Outputs (in --output-dir, default: paths.report_dir):
    classification.json
    classification.csv
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from aap_migration.ai.classify import classify_failures, format_csv, load_failed_rows
from aap_migration.ai.client import is_enabled as ai_is_enabled
from aap_migration.cli.context import MigrationContext
from aap_migration.cli.decorators import handle_errors, pass_context
from aap_migration.cli.utils import echo_error, echo_info, echo_success, echo_warning


@click.command(name="classify-failures")
@click.option(
    "--db",
    "db",
    type=str,
    help="State database: SQLAlchemy URL or SQLite file (default: state.db_path from --config).",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    help="Where to write classification.json/.csv (default: paths.report_dir, else reports/).",
)
@click.option(
    "--ai/--no-ai",
    "use_ai",
    default=None,
    help="Add AI explanations per failure group (default: on when AI_ENDPOINT is set).",
)
@pass_context
@handle_errors
def classify_failures_cmd(
    ctx: MigrationContext, db: str | None, output_dir: Path | None, use_ai: bool | None
) -> None:
    """Classify every failed resource with an RC code (or UNCLASSIFIED).

    \b
    Examples:
        aap-migrate classify-failures --db migration_state.db --no-ai
        aap-migrate --config config/config.yaml classify-failures
    """
    config = ctx.config if ctx.config_path else None
    if not db:
        if config is None:
            echo_error("Pass --db or --config so the state database can be found.")
            raise click.exceptions.Exit(2)
        db = str(config.state.db_path)
    if output_dir is None:
        output_dir = Path(config.paths.report_dir) if config else Path("reports")
    output_dir.mkdir(parents=True, exist_ok=True)

    if use_ai and not ai_is_enabled():
        echo_warning("--ai given but AI_ENDPOINT is not set: continuing without AI explanations.")
        use_ai = False

    echo_info("Reading failed resources from the state database (read-only) ...")
    rows = load_failed_rows(db)
    result = classify_failures(rows, use_ai=use_ai)

    json_path = output_dir / "classification.json"
    csv_path = output_dir / "classification.csv"
    json_path.write_text(json.dumps(result.to_dict(), indent=1), encoding="utf-8")
    csv_path.write_text(format_csv(result), encoding="utf-8")

    echo_success(f"Written {json_path}")
    echo_success(f"Written {csv_path}")
    click.echo()
    click.echo(f"  {len(result.resources)} failed resources")
    for rc_code, count in result.per_rc.items():
        if count:
            click.echo(f"  {rc_code:<13} {count}")
    if result.ai_used:
        explained = sum(1 for g in result.groups if g.ai_explanation)
        click.echo(f"  AI explanations: {explained}/{len(result.groups)} groups")

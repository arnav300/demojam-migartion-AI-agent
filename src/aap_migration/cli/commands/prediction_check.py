"""``prediction-check``: compare the pre-migration prediction with what really failed."""

from __future__ import annotations

import json
from pathlib import Path

import click

from aap_migration.analysis.prediction_check import compare_predictions, load_actual_rows
from aap_migration.cli.context import MigrationContext
from aap_migration.cli.decorators import handle_errors, pass_context
from aap_migration.cli.utils import echo_error, echo_success


@click.command(name="prediction-check")
@click.option(
    "--prediction",
    "prediction_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="premigration-report.json written before the migration.",
)
@click.option("--db", type=str, help="State database (default: state.db_path from --config).")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Where to write prediction-check.json (default: paths.report_dir, else reports/).",
)
@pass_context
@handle_errors
def prediction_check(
    ctx: MigrationContext, prediction_file: Path, db: str | None, output_dir: Path | None
) -> None:
    """Did the pre-migration report foresee the real failures? (rules only, no AI)

    \b
    Examples:
        aap-migrate prediction-check --prediction reports/premigration-report.json --db migration_state.db
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

    report = json.loads(prediction_file.read_text(encoding="utf-8"))
    comparison = compare_predictions(report, load_actual_rows(db))
    path = output_dir / "prediction-check.json"
    path.write_text(json.dumps(comparison, indent=1), encoding="utf-8")

    t, m = comparison["totals"], comparison["metrics"]
    click.echo()
    click.echo(f"  Predicted to fail : {t['predicted_failures']}")
    click.echo(f"  Actually failed   : {t['actual_failures']}")
    click.echo(f"  Foreseen          : {t['confirmed']} (recall {m['recall_pct']}%)")
    click.echo(f"  Same root cause   : {t['confirmed_same_rc']} ({m['rc_agreement_pct']}% of foreseen)")
    click.echo(f"  Missed            : {t['missed']}")
    click.echo(f"  False alarms      : {t['false_alarm']}")
    click.echo()
    echo_success(f"Written {path}")

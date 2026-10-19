"""``cac-export``: one organization as configuration as code (T19).

Reads the staged AAP 2.4 export only and writes ``infra.aap_configuration``
var files. Nothing is read from or written to AAP.
"""

from __future__ import annotations

from pathlib import Path

import click

from aap_migration.cac.exporter import export_organization, write_cac
from aap_migration.cli.context import MigrationContext
from aap_migration.cli.decorators import handle_errors, pass_context
from aap_migration.cli.utils import echo_error, echo_info, echo_success, echo_warning


@click.command(name="cac-export")
@click.option("--org", "--organization", "organization", required=True, help="Organization name.")
@click.option(
    "--export-dir",
    type=click.Path(path_type=Path),
    help="Staged export directory (default: paths.export_dir, else exports/).",
)
@click.option(
    "--out",
    "out_dir",
    default="cac",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Output directory; files go to <out>/<organization>/.",
)
@pass_context
@handle_errors
def cac_export(ctx: MigrationContext, organization: str, export_dir: Path | None, out_dir: Path) -> None:
    """Export ONE organization as infra.aap_configuration var files.

    \b
    Names replace IDs, and every secret becomes a {{ vault_* }} placeholder.

    \b
    Examples:
        aap-migrate cac-export --org Finance --export-dir exports --out cac/
        aap-migrate --config config/config.yaml cac-export --org Finance
    """
    if export_dir is None:
        export_dir = Path(ctx.config.paths.export_dir) if ctx.config_path else Path("exports")
    if not export_dir.is_dir():
        echo_error(f"No export found in '{export_dir}'. Run 'aap-migrate export' first.")
        raise click.exceptions.Exit(2)
    try:
        result = export_organization(export_dir, organization)
    except ValueError as e:
        echo_error(str(e))
        raise click.exceptions.Exit(2) from e

    written = write_cac(result, out_dir)
    for var, n in result.counts().items():
        if n:
            echo_info(f"{var}: {n}")
    for item in result.skipped:
        echo_warning(f"Skipped: {item}")
    echo_success(f"Written {len(written)} files to {written[0].parent} ({len(result.vault_vars)} vault placeholders)")

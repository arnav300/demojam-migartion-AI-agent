"""``dashboard``: live source vs target counts while a migration runs (C11)."""

from __future__ import annotations

from pathlib import Path

import click

from aap_migration.cli.context import MigrationContext
from aap_migration.cli.decorators import handle_errors, pass_context
from aap_migration.cli.utils import echo_info, echo_warning
from aap_migration.dashboard.counts import CountsProvider, TargetCounter
from aap_migration.dashboard.server import make_server


@click.command(name="dashboard")
@click.option("--port", default=8080, show_default=True, type=int, help="Port to listen on.")
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Address to bind (use 0.0.0.0 in a container).",
)
@click.option(
    "--export-dir",
    type=click.Path(path_type=Path),
    help="Export directory for source counts (default: paths.export_dir).",
)
@click.option(
    "--db",
    type=str,
    help="State database URL or SQLite file (default: state.db_path from --config).",
)
@pass_context
@handle_errors
def dashboard(
    ctx: MigrationContext, port: int, host: str, export_dir: Path | None, db: str | None
) -> None:
    """Serve a page with source, target and migrated counts, refreshed every 5 s.

    Only reads: the export directory, the state database and GET calls to the
    AAP 2.6 target. Stop with Ctrl+C.

    \b
    Examples:
        aap-migrate --config config/config.yaml dashboard --port 8080
        aap-migrate dashboard --export-dir exports --db migration_state.db
    """
    config = ctx.config if ctx.config_path else None
    export_dir = export_dir or (Path(config.paths.export_dir) if config else None)
    db = db or (str(config.state.db_path) if config else None)
    target = None
    if config is not None:
        target = TargetCounter(
            config.target.url, config.target.token, verify_ssl=config.target.verify_ssl
        )
    else:
        echo_warning("No --config: target counts are not available.")

    server = make_server(host, port, CountsProvider(export_dir, db, target))
    echo_info(f"Dashboard on http://{host}:{port}/ (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

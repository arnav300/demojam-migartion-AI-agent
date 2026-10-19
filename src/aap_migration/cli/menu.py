import subprocess
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from aap_migration.ai.client import is_enabled as ai_is_enabled
from aap_migration.cli.import_menu import import_submenu


def run_command(args: list[str], ctx: Any = None) -> None:
    """Run a CLI command in a subprocess."""
    # Use the same executable entry point
    cmd = [sys.argv[0]]

    # Pass config file if present in context - insert BEFORE subcommand args
    if ctx and ctx.obj and ctx.obj.config_path:
        cmd.extend(["--config", str(ctx.obj.config_path)])

    # Add subcommand and its args
    cmd.extend(args)

    try:
        subprocess.run(cmd, check=False)
    except Exception as e:
        print(f"Error running command: {e}")


def interactive_menu(ctx: Any) -> None:
    """Display interactive menu for AAP Bridge."""
    console = Console()

    while True:
        console.clear()
        console.print(
            Panel.fit(
                "[bold cyan]AAP Bridge - Migration Tool[/bold cyan]\n\n"
                "1. Prep Phase (Discover & Schema)\n"
                "2. Export (All)\n"
                "p. Pre-Migration Analysis (Dependency Analyzer + Readiness)\n"
                "3. Transform (All)\n"
                "4. Import Resources (Enhanced)\n"
                "5. Cleanup\n"
                "6. After Migration (Failures, Remediation, Reports, Dashboard)\n"
                "q. Quit",
                title="Main Menu",
                border_style="blue",
            )
        )

        choice = Prompt.ask(
            "Select an option", choices=["1", "2", "p", "3", "4", "5", "6", "q"], default="q"
        )

        if choice.lower() == "q":
            break

        console.print()  # Spacer

        if choice == "1":
            run_command(["prep"], ctx)
            Prompt.ask("\nPress Enter to return to menu...")
        elif choice == "2":
            run_command(["export"], ctx)
            Prompt.ask("\nPress Enter to return to menu...")
        elif choice == "p":
            premigration_submenu(ctx)
        elif choice == "3":
            run_command(["transform"], ctx)
            Prompt.ask("\nPress Enter to return to menu...")
        elif choice == "4":
            # Launch enhanced import submenu
            import_submenu(ctx)
        elif choice == "5":
            run_command(["cleanup"], ctx)
            Prompt.ask("\nPress Enter to return to menu...")
        elif choice == "6":
            post_migration_submenu(ctx)


def premigration_submenu(ctx: Any) -> None:
    """Before-migration analysis: read-only on AAP 2.4, nothing written to 2.6."""
    console = Console()
    report_dir = "reports"
    try:
        report_dir = ctx.obj.config.paths.report_dir
    except Exception:
        pass
    deps_json = f"{report_dir}/dependencies.json"
    deps_html = f"{report_dir}/dependencies.html"
    risk_json = f"{report_dir}/premigration-report.json"

    while True:
        console.clear()
        console.print(
            Panel.fit(
                "[bold cyan]Pre-Migration Analysis[/bold cyan] [dim](read-only on AAP 2.4)[/dim]\n\n"
                "1. Dependency Analyzer (all organizations → HTML + JSON)\n"
                "2. Pre-migration readiness report (from export)\n"
                "3. Pre-migration readiness report (live from AAP 2.4 API)\n"
                "4. Re-render Dependency Analyzer with readiness data\n"
                "5. Run everything: Export → Readiness → Dependency Analyzer\n"
                "b. Back",
                title="Before Migration",
                border_style="cyan",
            )
        )
        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "5", "b"], default="b")
        if choice == "b":
            return
        console.print()
        if choice == "1":
            run_command(["analyze-dependencies", "--all", "--output-html", deps_html, "--output-json", deps_json], ctx)
        elif choice == "2":
            run_command(["premigration-report", "--format", "all"], ctx)
        elif choice == "3":
            run_command(["premigration-report", "--source", "live", "--format", "all"], ctx)
        elif choice == "4":
            run_command(["render-dependency-report", "--input-json", deps_json,
                         "--risk-report", risk_json, "--output-html", deps_html], ctx)
        elif choice == "5":
            run_command(["export", "--yes", "--force"], ctx)
            run_command(["premigration-report", "--format", "all"], ctx)
            run_command(["analyze-dependencies", "--all", "--output-html", deps_html,
                         "--output-json", deps_json, "--risk-report", risk_json], ctx)
        Prompt.ask("\nPress Enter to return to menu...")


def post_migration_submenu(ctx: Any) -> None:
    """After migration: classify failures, draft remediation, reports and live dashboard."""
    console = Console()
    report_dir = "reports"
    try:
        report_dir = ctx.obj.config.paths.report_dir
    except Exception:
        pass
    classification = f"{report_dir}/classification.json"
    ai_state = "[green]on[/green]" if ai_is_enabled() else "[dim]off (AI_ENDPOINT not set)[/dim]"

    while True:
        console.clear()
        console.print(
            Panel.fit(
                "[bold cyan]After Migration[/bold cyan] [dim](reads the state database)[/dim]\n"
                f"AI layer: {ai_state}\n\n"
                "1. Enhanced report (HTML, with AI summary when on)\n"
                "2. Classify failures by root cause (JSON + CSV)\n"
                "3. Remediation playbooks from the classification (check mode)\n"
                "4. Live dashboard on port 8080 (Ctrl+C to stop)\n"
                "b. Back",
                title="After Migration",
                border_style="cyan",
            )
        )
        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "b"], default="b")
        if choice == "b":
            return
        console.print()
        if choice == "1":
            run_command(["enhanced-report"], ctx)
        elif choice == "2":
            run_command(["classify-failures", "--output-dir", report_dir], ctx)
        elif choice == "3":
            run_command(["remediate", "--from", classification, "--out", "remediation"], ctx)
        elif choice == "4":
            run_command(["dashboard", "--port", "8080"], ctx)
        Prompt.ask("\nPress Enter to return to menu...")

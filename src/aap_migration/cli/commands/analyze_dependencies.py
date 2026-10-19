"""CLI command for analyzing cross-organization dependencies."""

from __future__ import annotations

import asyncio
import json
import sys

import click

from aap_migration.analysis.dependency_analyzer import CrossOrgDependencyAnalyzer
from aap_migration.analysis.html_report import (
    generate_html_report,
    render_dependency_html,
    view_model_from_json,
)
from aap_migration.analysis.reports import (
    format_detailed_report,
    format_summary_report,
)
from aap_migration.cli.context import MigrationContext
from aap_migration.cli.decorators import pass_context
from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)


def serialize_global_report_to_json(report) -> str:
    """Convert GlobalDependencyReport to JSON string.

    Args:
        report: GlobalDependencyReport object

    Returns:
        JSON string representation
    """
    # Build organizations dict with full details
    organizations = {}
    for org_name, org_report in report.org_reports.items():
        # Build dependencies dict
        dependencies = {}
        for dep_org, dep_resources in org_report.dependencies.items():
            dependencies[dep_org] = [
                {
                    "resource_type": dep.resource_type,
                    "resource_id": dep.resource_id,
                    "resource_name": dep.resource_name,
                    "used_by": dep.required_by,
                }
                for dep in dep_resources
            ]

        organizations[org_name] = {
            "org_id": org_report.org_id,
            "resource_summary": sorted(
                (
                    {"type": rtype, "count": len(items)}
                    for rtype, items in (org_report.resources or {}).items()
                    if items
                ),
                key=lambda x: -x["count"],
            ),
            "resource_count": org_report.resource_count,
            "has_dependencies": org_report.has_cross_org_deps,
            "can_migrate_standalone": org_report.can_migrate_standalone,
            "required_before": org_report.required_migrations_before,
            "dependencies": dependencies,
        }

    # Build final report structure
    report_dict = {
        "analysis_date": report.analysis_date.isoformat(),
        "source_url": report.source_url,
        "total_organizations": report.total_organizations,
        "analyzed_organizations": report.analyzed_organizations,
        "independent_orgs": report.independent_orgs,
        "dependent_orgs": report.dependent_orgs,
        "migration_order": report.migration_order,
        "migration_phases": report.migration_phases,
        "cycles": [list(c) for c in (getattr(report, "cycles", None) or [])],
        "organizations": organizations,
    }

    return json.dumps(report_dict, indent=2)


def _load_risk_report(path: str | None) -> dict | None:
    """Load a premigration-report JSON, validating its type."""
    if not path:
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("report_type") != "premigration":
        raise click.ClickException(f"{path} is not a premigration-report JSON file")
    click.echo(f"ℹ Using pre-migration data from {path} (generated {data.get('generated')})")
    return data


@click.command(name="analyze-dependencies")
@click.option(
    "-o",
    "--organization",
    multiple=True,
    help="Organization name(s) to analyze. Can specify multiple times.",
)
@click.option(
    "--all",
    "analyze_all",
    is_flag=True,
    help="Analyze all organizations in source AAP.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed analysis for each organization (Format B).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "html", "json"], case_sensitive=False),
    default="text",
    help="Output format: text (default), html, or json. Deprecated: use --output-html or --output-json instead.",
)
@click.option(
    "--output",
    "-out",
    "output_file",
    type=click.Path(),
    help="Output file path. Deprecated: use --output-html or --output-json instead.",
)
@click.option(
    "--output-html",
    type=click.Path(),
    help="Generate HTML report at specified path.",
)
@click.option(
    "--output-json",
    type=click.Path(),
    help="Generate JSON report at specified path.",
)
@click.option(
    "--risk-report",
    type=click.Path(exists=True, dir_okay=False),
    help="premigration-report JSON: annotate the HTML with predicted failures and stale data.",
)
@pass_context
def analyze_dependencies_cmd(
    ctx: MigrationContext,
    organization: tuple[str, ...],
    analyze_all: bool,
    verbose: bool,
    output_format: str,
    output_file: str | None,
    output_html: str | None,
    output_json: str | None,
    risk_report: str | None,
):
    """Analyze cross-organization dependencies for migration planning.

    This tool helps you understand which organizations depend on others,
    allowing you to plan the correct migration order.

    Examples:

        # Analyze specific organization
        aap-bridge analyze-dependencies -o "Default"

        # Analyze multiple organizations
        aap-bridge analyze-dependencies -o "Default" -o "Engineering"

        # Analyze all organizations
        aap-bridge analyze-dependencies --all

        # Detailed view for single org
        aap-bridge analyze-dependencies -o "Default" --verbose

        # Generate HTML report
        aap-bridge analyze-dependencies --all --output-html report.html

        # Generate JSON report for automation
        aap-bridge analyze-dependencies --all --output-json deps.json

        # Generate both HTML and JSON
        aap-bridge analyze-dependencies --all --output-html report.html --output-json deps.json

        # Enrich the HTML with the pre-migration readiness report
        aap-bridge analyze-dependencies --all --output-html deps.html --risk-report reports/premigration-report.json
    """
    asyncio.run(run_analysis(ctx, organization, analyze_all, verbose, output_format, output_file, output_html, output_json, risk_report))


async def run_analysis(
    ctx: MigrationContext,
    organizations: tuple[str, ...],
    analyze_all: bool,
    verbose: bool,
    output_format: str,
    output_file: str | None,
    output_html: str | None,
    output_json: str | None,
    risk_report: str | None = None,
):
    """Run dependency analysis."""
    try:
        risk_data = _load_risk_report(risk_report)
        # Validate input
        if not analyze_all and not organizations:
            click.echo("Error: Must specify --all or -o ORGANIZATION")
            click.echo("Run 'aap-bridge analyze-dependencies --help' for usage")
            sys.exit(1)

        if analyze_all and organizations:
            click.echo("Error: Cannot use --all with -o ORGANIZATION")
            sys.exit(1)

        # Handle old-style --format + --output for backwards compatibility
        if output_file:
            if output_format == "html":
                output_html = output_file
            elif output_format == "json":
                output_json = output_file

        # Validate new-style options
        if output_format in ("html", "json") and not output_file and not output_html and not output_json:
            click.echo(f"Error: --output is required when using --format {output_format}")
            sys.exit(1)

        # Create analyzer
        analyzer = CrossOrgDependencyAnalyzer(ctx.source_client)

        if analyze_all:
            # Analyze all organizations
            click.echo("ℹ Analyzing all organizations in source AAP...")
            click.echo("")

            global_report = await analyzer.analyze_all_organizations()

            # Generate HTML if requested
            if output_html:
                html_content = generate_html_report(global_report, risk_data)
                with open(output_html, "w", encoding="utf-8") as f:
                    f.write(html_content)
                click.echo(f"✓ HTML report generated: {output_html}")

            # Generate JSON if requested
            if output_json:
                json_content = serialize_global_report_to_json(global_report)
                with open(output_json, "w", encoding="utf-8") as f:
                    f.write(json_content)
                click.echo(f"✓ JSON report generated: {output_json}")

            # Print to console if no output files specified
            if not output_html and not output_json:
                click.echo(format_summary_report(global_report))

        elif len(organizations) == 1:
            # Single organization - show detailed report
            org_name = organizations[0]
            click.echo(f"ℹ Analyzing organization: {org_name}")
            click.echo("")

            org_report = await analyzer.analyze_organization(org_name)

            # If output requested, build global report
            if output_html or output_json:
                from datetime import datetime
                from aap_migration.analysis.dependency_graph import (
                    group_into_phases,
                    topological_sort,
                )
                from aap_migration.analysis.dependency_analyzer import (
                    GlobalDependencyReport,
                )

                independent = [] if org_report.has_cross_org_deps else [org_name]
                dependent = [org_name] if org_report.has_cross_org_deps else []
                graph = {org_name: org_report.required_migrations_before}
                migration_order = topological_sort(graph)
                migration_phases = group_into_phases(graph, migration_order)

                global_report = GlobalDependencyReport(
                    analysis_date=datetime.now(),
                    source_url=str(ctx.source_client.base_url),
                    total_organizations=1,
                    analyzed_organizations=[org_name],
                    independent_orgs=independent,
                    dependent_orgs=dependent,
                    org_reports={org_name: org_report},
                    migration_order=migration_order,
                    migration_phases=migration_phases,
                )

                # Generate HTML if requested
                if output_html:
                    html_content = generate_html_report(global_report, risk_data)
                    with open(output_html, "w", encoding="utf-8") as f:
                        f.write(html_content)
                    click.echo(f"✓ HTML report generated: {output_html}")

                # Generate JSON if requested
                if output_json:
                    json_content = serialize_global_report_to_json(global_report)
                    with open(output_json, "w", encoding="utf-8") as f:
                        f.write(json_content)
                    click.echo(f"✓ JSON report generated: {output_json}")
            else:
                # Print detailed report to console
                click.echo(format_detailed_report(org_report))

        else:
            # Multiple organizations - show summary
            click.echo(f"ℹ Analyzing {len(organizations)} organizations...")
            click.echo("")

            org_reports = {}
            for org_name in organizations:
                org_report = await analyzer.analyze_organization(org_name)
                org_reports[org_name] = org_report

            # Build mini global report
            from datetime import datetime

            from aap_migration.analysis.dependency_graph import (
                group_into_phases,
                topological_sort,
            )

            independent = sorted([name for name, r in org_reports.items()
                                  if not r.has_cross_org_deps])
            dependent = sorted([name for name, r in org_reports.items()
                                if r.has_cross_org_deps])

            graph = {org: report.required_migrations_before
                     for org, report in org_reports.items()}
            migration_order = topological_sort(graph)
            migration_phases = group_into_phases(graph, migration_order)

            # Create global report for these orgs
            from aap_migration.analysis.dependency_analyzer import (
                GlobalDependencyReport,
            )

            global_report = GlobalDependencyReport(
                analysis_date=datetime.now(),
                source_url=str(ctx.source_client.base_url),
                total_organizations=len(organizations),
                analyzed_organizations=list(organizations),
                independent_orgs=independent,
                dependent_orgs=dependent,
                org_reports=org_reports,
                migration_order=migration_order,
                migration_phases=migration_phases,
            )

            # Generate HTML if requested
            if output_html:
                html_content = generate_html_report(global_report, risk_data)
                with open(output_html, "w", encoding="utf-8") as f:
                    f.write(html_content)
                click.echo(f"✓ HTML report generated: {output_html}")

            # Generate JSON if requested
            if output_json:
                json_content = serialize_global_report_to_json(global_report)
                with open(output_json, "w", encoding="utf-8") as f:
                    f.write(json_content)
                click.echo(f"✓ JSON report generated: {output_json}")

            # Print to console if no output files specified
            if not output_html and not output_json:
                click.echo(format_summary_report(global_report))

                # If verbose, also print detailed for each org
                if verbose:
                    click.echo("")
                    click.echo("=" * 67)
                    click.echo("DETAILED ANALYSIS")
                    click.echo("=" * 67)
                    click.echo("")
                    for org_name in organizations:
                        click.echo(format_detailed_report(org_reports[org_name]))
                        click.echo("")

    except Exception as e:
        logger.error(
            "dependency_analysis_failed",
            error=str(e),
            message=f"Dependency analysis failed: {e}"
        )
        click.echo(f"✗ Analysis failed: {e}")
        sys.exit(1)


@click.command(name="render-dependency-report")
@click.option(
    "--input-json", "input_json", required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON written by 'analyze-dependencies --output-json'.",
)
@click.option(
    "--risk-report",
    type=click.Path(exists=True, dir_okay=False),
    help="premigration-report JSON used to annotate predicted failures.",
)
@click.option("--output-html", required=True, type=click.Path(dir_okay=False), help="HTML file to write.")
def render_dependency_report_cmd(input_json: str, risk_report: str | None, output_html: str) -> None:
    """Re-render the Dependency Analyzer UI offline, without querying AAP.

    Combines an existing dependency JSON with a pre-migration report, so the
    (slow) dependency analysis and the export can run in parallel and be
    merged afterwards.

    \b
    Example:
        aap-bridge render-dependency-report \\
            --input-json reports/dependencies.json \\
            --risk-report reports/premigration-report.json \\
            --output-html reports/dependencies.html
    """
    with open(input_json, encoding="utf-8") as f:
        deps = json.load(f)
    view = view_model_from_json(deps)
    html_content = render_dependency_html(view, _load_risk_report(risk_report))
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html_content)
    click.echo(f"✓ HTML report generated: {output_html}")

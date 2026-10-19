"""
Pre-migration readiness report (BEFORE migration).

Counterpart of ``enhanced-report`` (``migration_report_v2``): instead of reading
the migration state database after an import, it reads the RAW AAP 2.4 data
and predicts what will fail, what needs manual action and what is stale.

Data sources:
    export  (default) RAW files written by ``aap-bridge export``
    live    read-only GET calls against the AAP 2.4 REST API

Outputs (in --output-dir, default: reports/):
    premigration-report.html   interactive report for humans
    premigration-report.json   machine contract (Dependency Analyzer UI, RHOAI)
    premigration-report.csv    flat list of findings
    premigration-report.md     summary for Git / wiki / Slack

Usage:
    aap-bridge premigration-report
    aap-bridge premigration-report --source live
    aap-bridge premigration-report --format html --organization Finance
"""

from __future__ import annotations

import asyncio
import csv as csv_module
import dataclasses
import json
import re
from collections import Counter
from io import StringIO
from pathlib import Path

import click

from aap_migration.ai.client import is_enabled as ai_is_enabled
from aap_migration.ai.explain import CACHE_FILENAME, explain_findings
from aap_migration.analysis.premigration import (
    OUTCOME_LABELS,
    RC_CATALOG,
    SEVERITY_ORDER,
    build_report_dict,
    fetch_inventory_live,
    load_inventory_from_export,
    load_schema_comparison,
    run_analysis,
)
from aap_migration.cli.context import MigrationContext
from aap_migration.cli.decorators import handle_errors, pass_context, requires_config
from aap_migration.cli.utils import echo_error, echo_info, echo_success, echo_warning
from aap_migration.reporting.premigration_html import render_premigration_html
from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

_FORMATS = ("html", "json", "csv", "markdown")


def filter_report_by_org(report: dict, organization: str) -> dict:
    """Restrict a report dict to one organization (case-insensitive)."""
    wanted = {n for n in report["organizations"] if n.lower() == organization.lower()}
    if not wanted:
        similar = [n for n in report["organizations"] if organization.lower() in n.lower()]
        hint = f" Similar: {', '.join(similar[:5])}" if similar else ""
        raise click.ClickException(f"Organization '{organization}' not found.{hint}")
    findings = [f for f in report["findings"] if f["organization"] in wanted]
    resources = {k: r for k, r in report["resources"].items() if r["organization"] in wanted}
    orgs = {n: o for n, o in report["organizations"].items() if n in wanted}

    outcomes: Counter = Counter()
    counts: Counter = Counter()
    for o in orgs.values():
        outcomes.update(o["outcomes"])
        counts.update(o["by_type"])
    total = sum(o["total"] for o in orgs.values())
    by_type: dict[str, Counter] = {}
    for r in resources.values():
        by_type.setdefault(r["resource_type"], Counter())[r["outcome"]] += 1
    rc_res: dict[str, set] = {}
    for f in findings:
        rc_res.setdefault(f["rc"], set()).add(f["key"])

    s = report["summary"]
    s.update({
        "total_resources": total,
        "inventory_counts": dict(counts),
        "outcomes": {k: outcomes.get(k, 0) for k in OUTCOME_LABELS},
        "findings_total": len(findings),
        "findings_by_severity": {k: sum(1 for f in findings if f["severity"] == k) for k in SEVERITY_ORDER},
        "findings_by_rc": dict(Counter(f["rc"] for f in findings)),
        "resources_by_rc": {k: len(v) for k, v in rc_res.items()},
        "outcomes_by_type": {k: dict(v) for k, v in by_type.items()},
        "predicted_success_pct": round(100 * (total - outcomes.get("will_fail", 0)) / total, 1) if total else 100.0,
        "organizations": len(orgs),
    })
    report.update({"findings": findings, "resources": resources, "organizations": orgs})
    report["filter"] = {"organization": sorted(wanted)[0]}
    return report


def _add_ai_explanations(
    report: dict, result, use_ai: bool | None, output_dir: Path, scoped: bool
) -> None:
    """Attach ``report["ai_explanations"]`` (C7) when AI is on; otherwise leave the report as is."""
    enabled = ai_is_enabled()
    if use_ai is None:
        use_ai = enabled
    if not use_ai:
        return
    if not enabled:
        echo_warning("--ai given but AI_ENDPOINT is not set: continuing without AI explanations.")
        return
    if scoped:
        # Only the filtered organization's findings may reach the prompt.
        result = dataclasses.replace(
            result, findings=[f for f in result.findings if f.organization in report["organizations"]]
        )
    echo_info("Asking the AI model to explain each root cause ...")
    explanations = explain_findings(result, cache_path=output_dir / CACHE_FILENAME)
    if explanations:
        report["ai_explanations"] = {rc: e.model_dump() for rc, e in explanations.items()}
        echo_info(f"AI explanations added for {len(explanations)} root causes")
    else:
        echo_warning("No AI explanations available; the report shows the rule-based text only.")


def format_markdown(report: dict) -> str:
    s = report["summary"]
    o = s["outcomes"]
    lines = [
        "# AAP 2.4 → 2.6 pre-migration readiness",
        "",
        f"Source: {report['source'].get('url') or 'n/a'} (AAP {report['source'].get('version') or '2.4'}), "
        f"generated {report['generated']}",
        "",
        f"**Predicted first-pass success: {s['predicted_success_pct']}%** across "
        f"{s['total_resources']} objects in {s['organizations']} organizations.",
        "",
        "| Outcome | Objects |",
        "|---|---:|",
    ]
    lines += [f"| {OUTCOME_LABELS[k]} | {o.get(k, 0)} |" for k in OUTCOME_LABELS]
    lines += ["", "## Root causes", "", "| Code | Root cause | Objects |", "|---|---|---:|"]
    for rc, n in sorted(s["resources_by_rc"].items()):
        lines.append(f"| {rc} | {RC_CATALOG[rc]['title']} | {n} |")
    lines += ["", "## Organizations needing attention", "",
              "| Organization | Objects | Will fail | At risk | Stale | First-pass success |",
              "|---|---:|---:|---:|---:|---:|"]
    ranked = sorted(
        report["organizations"].items(),
        key=lambda kv: (-(kv[1]["outcomes"].get("will_fail", 0) + kv[1]["outcomes"].get("at_risk", 0)), kv[0]),
    )
    for name, org in ranked[:25]:
        oc = org["outcomes"]
        lines.append(
            f"| {name} | {org['total']} | {oc.get('will_fail', 0)} | {oc.get('at_risk', 0)} | "
            f"{oc.get('stale', 0)} | {org['predicted_success_pct']}% |"
        )
    blocking = [f for f in report["findings"] if f["severity"] == "critical"]
    if blocking:
        lines += ["", "## Blocking findings (first 50)", "",
                  "| Code | Type | Resource | Organization | Problem |", "|---|---|---|---|---|"]
        for f in blocking[:50]:
            name = str(f["resource_name"]).replace("|", "\\|")
            lines.append(f"| {f['rc']} | {f['resource_type']} | {name} (#{f['resource_id']}) | "
                         f"{f['organization']} | {f['title']} |")
    return "\n".join(lines) + "\n"


def format_csv(report: dict) -> str:
    out = StringIO()
    w = csv_module.writer(out)
    w.writerow([
        "Severity", "Root Cause", "Root Cause Title", "Organization", "Resource Type", "Resource ID",
        "Resource Name", "Predicted Outcome", "Problem", "Detail", "Remediation", "Caused By",
        "Last Modified", "Modified By", "Last Job Run",
    ])
    for f in report["findings"]:
        r = report["resources"].get(f["key"], {})
        w.writerow([
            f["severity"], f["rc"], RC_CATALOG[f["rc"]]["title"], f["organization"], f["resource_type"],
            f["resource_id"], f["resource_name"], OUTCOME_LABELS.get(r.get("outcome", ""), ""),
            f["title"], f["detail"], f["remediation"], f.get("cascade_from") or "",
            r.get("modified", ""), r.get("modified_by", ""), r.get("last_job_run", ""),
        ])
    return out.getvalue()


def print_console_summary(report: dict) -> None:
    s = report["summary"]
    o = s["outcomes"]
    click.echo()
    click.echo(click.style(f"  {s['predicted_success_pct']}%", fg="white", bold=True) + click.style(
        f"  predicted first-pass success", fg="bright_black"))
    click.echo(click.style(
        f"  {s['total_resources']} objects · {s['organizations']} organizations", fg="bright_black"))
    click.echo()
    for k, c, icon in (
        ("will_fail", "red", "✗"), ("at_risk", "yellow", "!"), ("needs_action", "blue", "→"),
        ("stale", "magenta", "~"), ("ready", "green", "✓"),
    ):
        count = o.get(k, 0)
        if count:
            label = OUTCOME_LABELS[k]
            click.echo(f"  {click.style(icon, fg=c)}  {click.style(f'{count:>5,}', fg=c, bold=(k == 'will_fail'))}  {label}")
    click.echo()
    top_causes = sorted(s["resources_by_rc"].items(), key=lambda kv: -kv[1])[:5]
    if top_causes:
        click.echo(click.style("  Top root causes:", fg="bright_black"))
        for rc, n in top_causes:
            click.echo(f"    {click.style(f'{n:>4}', bold=True)}  {rc}  {RC_CATALOG[rc]['title']}")
    click.echo()


@click.command(name="premigration-report")
@click.option(
    "--source", "data_source",
    type=click.Choice(["export", "live"], case_sensitive=False),
    default="export", show_default=True,
    help="Read RAW data from the export directory, or live (read-only) from the AAP 2.4 API.",
)
@click.option("--export-dir", type=click.Path(path_type=Path), help="Export directory (default: paths.export_dir).")
@click.option("--output-dir", type=click.Path(path_type=Path), help="Where to write the reports (default: paths.report_dir).")
@click.option("--name", default="premigration-report", show_default=True, help="Base file name of the outputs.")
@click.option(
    "--format", "formats", multiple=True,
    type=click.Choice([*_FORMATS, "all"], case_sensitive=False),
    help="Output format(s). Repeatable. Default: html + json + csv.",
)
@click.option("--organization", "--org", type=str, help="Limit the report to one organization.")
@click.option(
    "--schema-comparison", type=click.Path(path_type=Path),
    help="schema_comparison.json from 'aap-bridge prep' to detect fields removed in 2.6 "
         "(default: schemas/schema_comparison.json when present).",
)
@click.option("--include-healthy", is_flag=True, help="Also list objects with no findings in the JSON/HTML.")
@click.option("--fail-on-blocking", is_flag=True,
              help="Exit with code 3 when objects are predicted to fail (useful in pipelines).")
@click.option("--ai/--no-ai", "use_ai", default=None,
              help="Add AI explanations per root cause (default: on when AI_ENDPOINT is set).")
@pass_context
@requires_config
@handle_errors
def generate_premigration_report(
    ctx: MigrationContext,
    data_source: str,
    export_dir: Path | None,
    output_dir: Path | None,
    name: str,
    formats: tuple[str, ...],
    organization: str | None,
    schema_comparison: Path | None,
    include_healthy: bool,
    fail_on_blocking: bool,
    use_ai: bool | None = None,
) -> None:
    """Predict migration failures and stale data from the AAP 2.4 source.

    \b
    Runs BEFORE migration. Nothing is written to either AAP: the report reads
    the RAW export (or GETs the source API) and applies the RC-001 → RC-011
    root-cause rules plus the staleness heuristics shared with
    'enhanced-report'.

    \b
    PREDICTED OUTCOMES
      Will fail      AAP 2.6 will reject the object as it is today
      At risk        likely to fail, or depends on something that fails
      Needs action   migrates, but secrets / auth / features need follow-up
      Stale          abandoned object, cleanup candidate
      Ready          nothing found

    Examples:

        # After 'aap-bridge export' (recommended)
        aap-bridge premigration-report

        # Without an export, straight from the AAP 2.4 API (read-only)
        aap-bridge premigration-report --source live

        # Only HTML, one organization
        aap-bridge premigration-report --format html --org Finance
    """
    config = ctx.config
    export_dir = Path(export_dir or config.paths.export_dir)
    output_dir = Path(output_dir or config.paths.report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats} or {"html", "json", "csv"}
    if "all" in fmts:
        fmts = set(_FORMATS)

    source_url = str(config.source.url)
    source_version = ""

    if data_source == "export":
        if not export_dir.is_dir() or not any(export_dir.iterdir()):
            raise click.ClickException(
                f"No export found in '{export_dir}'. Run 'aap-bridge export' first or use --source live."
            )
        echo_info(f"Loading RAW AAP 2.4 data from {export_dir} ...")
        inventory = load_inventory_from_export(export_dir)
        meta_file = export_dir / "metadata.json"
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                source_url = meta.get("source_url", source_url)
                source_version = meta.get("source_version", "")
                echo_info(f"Export taken at {meta.get('export_timestamp', 'unknown time')}")
            except json.JSONDecodeError:
                pass
    else:
        echo_info(f"Reading AAP 2.4 data live from {source_url} (read-only) ...")

        async def _fetch():
            client = ctx.source_client
            version = ""
            try:
                version = await client.get_version()
            except Exception:
                pass
            inv = await fetch_inventory_live(client, progress=echo_info)
            await client.close()
            return inv, version

        inventory, source_version = asyncio.run(_fetch())

    if not schema_comparison:
        default_schema = Path(getattr(config.paths, "schema_dir", "schemas")) / "schema_comparison.json"
        schema_comparison = default_schema if default_schema.is_file() else None
    schema = load_schema_comparison(schema_comparison)
    if schema:
        echo_info(f"Using schema comparison {schema_comparison} for AAP 2.4 → 2.6 field drift")
    else:
        echo_warning("No schema_comparison.json found: run 'aap-bridge prep' to also detect API field drift.")

    echo_info("Applying root-cause (RC-001 → RC-011) and staleness rules ...")
    result = run_analysis(
        inventory,
        source_url=source_url,
        source_version=source_version or "2.4",
        data_source=data_source,
        schema_comparison=schema,
        include_healthy=include_healthy,
    )
    report = build_report_dict(result)
    if organization:
        report = filter_report_by_org(report, organization)

    _add_ai_explanations(report, result, use_ai, output_dir, scoped=bool(organization))

    base = name
    if organization:
        safe_org = re.sub(r"[^\w\-]", "_", organization)
        base = f"{name}-{safe_org}"

    written = []
    if "json" in fmts:
        p = output_dir / f"{base}.json"
        p.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        written.append(p)
    if "html" in fmts:
        p = output_dir / f"{base}.html"
        p.write_text(render_premigration_html(report), encoding="utf-8")
        written.append(p)
    if "csv" in fmts:
        p = output_dir / f"{base}.csv"
        p.write_text(format_csv(report), encoding="utf-8")
        written.append(p)
    if "markdown" in fmts:
        p = output_dir / f"{base}.md"
        p.write_text(format_markdown(report), encoding="utf-8")
        written.append(p)

    for p in written:
        echo_success(f"Written {p} ({p.stat().st_size / 1024:.0f} KB)")
    print_console_summary(report)

    if fail_on_blocking and report["summary"]["outcomes"].get("will_fail", 0):
        echo_error("Objects predicted to fail were found (--fail-on-blocking).")
        raise click.exceptions.Exit(3)

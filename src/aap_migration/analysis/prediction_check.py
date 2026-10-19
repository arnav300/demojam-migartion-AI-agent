"""Predicted vs actual: did the pre-migration report foresee the real failures?

Rules only (no AI).  Joins the pre-migration report (``premigration-report.json``)
with the migration results by ``(resource_type, source_id)`` and compares both
the outcome and the RC code.  Actual RC codes come from the same rules as
``classify-failures`` (``normalize_error_key`` + ``rc_for_error``).

Categories per resource:
    confirmed     predicted to fail (will fail / at risk) and failed
    missed        failed although the report predicted nothing blocking
    false_alarm   predicted to fail but migrated fine
    not_migrated  predicted to fail and never imported (skipped / pending / absent)
"""

from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aap_migration.ai.classify import read_only_database_url
from aap_migration.ai.rc_map import UNCLASSIFIED, rc_for_error
from aap_migration.analysis.premigration import RC_CATALOG
from aap_migration.cli.commands.migration_report_v2 import normalize_error_key
from aap_migration.migration.models import MigrationProgress

PREDICTED_FAILING = {"will_fail", "at_risk"}
MAX_LISTED = 10


def load_actual_rows(db: str) -> list[dict[str, Any]]:
    """All rows of ``migration_progress`` (read-only)."""
    engine = create_engine(read_only_database_url(db))
    try:
        with Session(engine) as session:
            rows = session.execute(
                select(
                    MigrationProgress.resource_type,
                    MigrationProgress.source_id,
                    MigrationProgress.source_name,
                    MigrationProgress.status,
                    MigrationProgress.error_message,
                )
            ).all()
    finally:
        engine.dispose()
    return [dict(r._mapping) for r in rows]


def _predictions(report: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    predicted: dict[tuple[str, int], dict[str, Any]] = {}
    for f in report.get("findings", []):
        if f.get("resource_id") is None or f.get("rc") == "STALE":
            continue
        key = (f["resource_type"], int(f["resource_id"]))
        entry = predicted.setdefault(key, {
            "name": f.get("resource_name", ""),
            "organization": f.get("organization", ""),
            "outcome": report.get("resources", {}).get(f.get("key", ""), {}).get("outcome", ""),
            "rcs": set(),
        })
        entry["rcs"].add(f["rc"])
    return {k: v for k, v in predicted.items() if v["outcome"] in PREDICTED_FAILING}


def compare_predictions(
    report: dict[str, Any],
    actual_rows: list[dict[str, Any]],
    organizations: set[str] | None = None,
) -> dict[str, Any]:
    """Compare a pre-migration report with actual migration rows.

    ``organizations`` limits the predictions to those organizations (the actual
    rows are expected to be filtered the same way by the caller).
    """
    predicted = _predictions(report)
    if organizations is not None:
        wanted = {o.lower() for o in organizations}
        predicted = {k: v for k, v in predicted.items() if str(v["organization"]).lower() in wanted}
    actual: dict[tuple[str, int], dict[str, Any]] = {}
    for row in actual_rows:
        if row.get("source_id") is None:
            continue
        key = (row["resource_type"], int(row["source_id"]))
        status = row.get("status") or ""
        rc = None
        if status == "failed":
            rc = rc_for_error(normalize_error_key(row.get("error_message") or ""), row["resource_type"])
        actual[key] = {"status": status, "rc": rc, "name": row.get("source_name") or ""}

    rows = []
    for key in sorted(set(predicted) | set(actual)):
        p, a = predicted.get(key), actual.get(key)
        status = a["status"] if a else None
        if status == "failed":
            category = "confirmed" if p else "missed"
        elif p is None:
            continue
        elif status == "completed":
            category = "false_alarm"
        else:
            category = "not_migrated"
        rows.append({
            "resource_type": key[0],
            "source_id": key[1],
            "name": (p or {}).get("name") or (a or {}).get("name", ""),
            "organization": (p or {}).get("organization", ""),
            "category": category,
            "predicted_rc": sorted(p["rcs"]) if p else [],
            "actual_rc": a["rc"] if a else None,
            "rc_match": bool(p and a and a["rc"] in p["rcs"]),
        })

    counts = Counter(r["category"] for r in rows)
    confirmed = counts["confirmed"]
    actual_failures = confirmed + counts["missed"]
    rc_matches = sum(1 for r in rows if r["category"] == "confirmed" and r["rc_match"])
    per_rc: dict[str, Counter] = {}
    for r in rows:
        for rc in r["predicted_rc"]:
            per_rc.setdefault(rc, Counter())["predicted"] += 1
        if r["actual_rc"]:
            per_rc.setdefault(r["actual_rc"], Counter())["failed"] += 1
            if r["rc_match"]:
                per_rc[r["actual_rc"]]["confirmed"] += 1

    def pct(n: int, d: int) -> float | None:
        return round(100 * n / d, 1) if d else None

    return {
        "totals": {
            "predicted_failures": len(predicted),
            "actual_failures": actual_failures,
            "confirmed": confirmed,
            "confirmed_same_rc": rc_matches,
            "missed": counts["missed"],
            "false_alarm": counts["false_alarm"],
            "not_migrated": counts["not_migrated"],
        },
        "metrics": {
            "recall_pct": pct(confirmed, actual_failures),
            "precision_pct": pct(confirmed, confirmed + counts["false_alarm"]),
            "rc_agreement_pct": pct(rc_matches, confirmed),
        },
        "per_rc": {
            rc: {k: c.get(k, 0) for k in ("predicted", "failed", "confirmed")}
            for rc, c in sorted(per_rc.items(), key=lambda kv: (kv[0] == UNCLASSIFIED, kv[0]))
        },
        "resources": rows,
    }


def _tile(label: str, value: Any, note: str = "") -> str:
    shown = "—" if value is None else (f"{value}%" if isinstance(value, float) else f"{value:,}")
    return (
        '<div style="flex:1 1 130px;padding:10px 12px;border:1px solid var(--line,#e5e7eb);border-radius:8px">'
        f'<div style="font-size:0.75rem;color:var(--ink-2,#4b5563)">{escape(label)}</div>'
        f'<div style="font-size:1.5rem;font-weight:600;font-variant-numeric:tabular-nums">{shown}</div>'
        f'<div style="font-size:0.75rem;color:var(--ink-2,#4b5563)">{escape(note)}</div></div>'
    )


def render_prediction_panel(comparison: dict[str, Any]) -> str:
    """HTML panel for the enhanced report (numbers only, no AI)."""
    t, m = comparison["totals"], comparison["metrics"]
    headline = (
        f"Before migrating, the pre-migration report flagged {t['confirmed']:,} of the "
        f"{t['actual_failures']:,} objects that failed"
        + (f", {t['confirmed_same_rc']:,} with the same root cause." if t["confirmed"] else ".")
    )
    tiles = "".join([
        _tile("Predicted to fail", t["predicted_failures"]),
        _tile("Actually failed", t["actual_failures"]),
        _tile("Foreseen (recall)", m["recall_pct"], f"{t['confirmed']:,} confirmed"),
        _tile("Same root cause", m["rc_agreement_pct"], f"{t['confirmed_same_rc']:,} of confirmed"),
        _tile("Missed", t["missed"], "failed, not predicted"),
        _tile("False alarms", t["false_alarm"], "predicted, migrated fine"),
    ])
    rc_rows = "".join(
        f"<tr><td>{escape(rc)}</td><td>{escape(RC_CATALOG.get(rc, {}).get('title', 'Unclassified'))}</td>"
        f'<td style="text-align:right">{c["predicted"]:,}</td><td style="text-align:right">{c["failed"]:,}</td>'
        f'<td style="text-align:right">{c["confirmed"]:,}</td></tr>'
        for rc, c in comparison["per_rc"].items()
    )
    missed = [r for r in comparison["resources"] if r["category"] == "missed"][:MAX_LISTED]
    missed_html = ""
    if missed:
        items = "".join(
            f"<li>{escape(r['resource_type'])} · {escape(str(r['name']))} → {escape(str(r['actual_rc']))}</li>"
            for r in missed
        )
        missed_html = (
            '<p style="margin-top:10px;font-weight:600">Missed (first '
            f"{len(missed)}): rules to improve</p><ul style=\"margin:4px 0 0 18px\">{items}</ul>"
        )
    return (
        '<section id="prediction-check" aria-label="Predicted vs actual" style="max-width:1200px;'
        "margin:16px auto;padding:16px 20px;background:var(--surface,#fff);color:var(--ink,#111827);"
        'border:1px solid var(--line,#e5e7eb);border-radius:10px;font-size:0.95rem;line-height:1.45">'
        '<h2 style="font-size:1.1rem;margin:0 0 6px">Predicted vs actual</h2>'
        f'<p style="margin-bottom:10px">{escape(headline)}</p>'
        f'<div style="display:flex;flex-wrap:wrap;gap:8px">{tiles}</div>'
        '<div style="overflow-x:auto;margin-top:12px"><table style="width:100%;border-collapse:collapse;font-size:0.9rem">'
        '<thead><tr style="text-align:left;color:var(--ink-2,#4b5563)"><th>Code</th><th>Root cause</th>'
        '<th style="text-align:right">Predicted</th><th style="text-align:right">Failed</th>'
        f'<th style="text-align:right">Confirmed</th></tr></thead><tbody>{rc_rows}</tbody></table></div>'
        f"{missed_html}</section>"
    )

"""C9: executive narrative for the HTML reports.

The model sees aggregate counts only (totals, per-type results, per-RC counts,
top 5 failing organizations) and writes the sentences.  Every number shown in
the "Summary" panel comes from our data; :class:`ReportNarrative` rejects
model text that contains numbers.
"""

from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any

from aap_migration.ai.client import AIClient, is_enabled
from aap_migration.ai.prompts import build_narrative_prompt
from aap_migration.ai.rc_map import UNCLASSIFIED, rc_for_error
from aap_migration.ai.schemas import ReportNarrative
from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

TOP_ORGS = 5


def premigration_aggregates(report: dict[str, Any]) -> dict[str, Any]:
    """Aggregates of a pre-migration report dict (``build_report_dict`` output)."""
    summary = report["summary"]
    ranked = sorted(
        report["organizations"].items(),
        key=lambda kv: (
            -(kv[1]["outcomes"].get("will_fail", 0) + kv[1]["outcomes"].get("at_risk", 0)),
            kv[0],
        ),
    )
    top = [
        {
            "organization": name,
            "will_fail": org["outcomes"].get("will_fail", 0),
            "at_risk": org["outcomes"].get("at_risk", 0),
        }
        for name, org in ranked
        if org["outcomes"].get("will_fail", 0) + org["outcomes"].get("at_risk", 0)
    ][:TOP_ORGS]
    return {
        "report": "pre-migration readiness (prediction, nothing migrated yet)",
        "totals": {
            "objects": summary["total_resources"],
            "organizations": summary["organizations"],
            **summary["outcomes"],
            "predicted_success_pct": summary["predicted_success_pct"],
        },
        "per_type": summary["outcomes_by_type"],
        "per_rc": dict(sorted(summary["resources_by_rc"].items())),
        "top_failing_orgs": top,
    }


def enhanced_aggregates(org_summary: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Aggregates of the post-migration ``enhanced-report`` organization summary."""
    totals: Counter = Counter()
    per_type: dict[str, Counter] = {}
    per_rc: Counter = Counter()
    failing: list[tuple[str, int]] = []
    for org_name, summary in org_summary.items():
        org_failed = 0
        for resource in summary["resources"]:
            status = resource.get("status", "failed")
            totals[status] += 1
            rtype = resource.get("resource_type", "")
            per_type.setdefault(rtype, Counter())[status] += 1
            if status == "failed":
                org_failed += 1
                per_rc[rc_for_error(resource.get("error_key") or "", rtype)] += 1
        if org_failed:
            failing.append((org_name, org_failed))
    failing.sort(key=lambda kv: (-kv[1], kv[0]))
    return {
        "report": "post-migration results",
        "totals": {
            "objects": sum(totals.values()),
            "organizations": len(org_summary),
            **{k: totals.get(k, 0) for k in ("completed", "failed", "skipped", "pending")},
        },
        "per_type": {k: dict(sorted(v.items())) for k, v in sorted(per_type.items())},
        "per_rc": dict(sorted(per_rc.items(), key=lambda kv: (kv[0] == UNCLASSIFIED, kv[0]))),
        "top_failing_orgs": [
            {"organization": name, "failed": n} for name, n in failing[:TOP_ORGS]
        ],
    }


def build_narrative(
    aggregates: dict[str, Any], client: AIClient | None = None
) -> ReportNarrative | None:
    """Ask the model for the narrative; None when AI is off or the answer is invalid."""
    if client is None:
        if not is_enabled():
            return None
        client = AIClient()
    system, user = build_narrative_prompt(aggregates)
    return client.complete_json(system, user, ReportNarrative)


def _fmt(n: Any) -> str:
    return f"{n:,}" if isinstance(n, int) else escape(str(n))


def _facts_premigration(agg: dict[str, Any]) -> tuple[str, str, str]:
    t = agg["totals"]
    migrated = (
        f"{_fmt(t.get('ready', 0))} ready · {_fmt(t.get('needs_action', 0))} need action · "
        f"{_fmt(t.get('stale', 0))} stale · of {_fmt(t['objects'])} objects"
    )
    failed = f"{_fmt(t.get('will_fail', 0))} will fail · {_fmt(t.get('at_risk', 0))} at risk"
    return migrated, failed, _facts_orgs(agg, ("will_fail", "at_risk"))


def _facts_enhanced(agg: dict[str, Any]) -> tuple[str, str, str]:
    t = agg["totals"]
    migrated = f"{_fmt(t['completed'])} of {_fmt(t['objects'])} objects migrated"
    failed = f"{_fmt(t['failed'])} failed · {_fmt(t['skipped'])} skipped · {_fmt(t['pending'])} pending"
    return migrated, failed, _facts_orgs(agg, ("failed",))


def _facts_orgs(agg: dict[str, Any], keys: tuple[str, ...]) -> str:
    rcs = sorted(agg["per_rc"].items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    parts = []
    if rcs:
        parts.append("Top causes: " + ", ".join(f"{escape(rc)} ({_fmt(n)})" for rc, n in rcs))
    if agg["top_failing_orgs"]:
        parts.append(
            "Organizations: "
            + ", ".join(
                f"{escape(o['organization'])} ({_fmt(sum(o.get(k, 0) for k in keys))})"
                for o in agg["top_failing_orgs"]
            )
        )
    return " · ".join(parts) or "No failures"


def render_summary_panel(narrative: ReportNarrative, aggregates: dict[str, Any], kind: str) -> str:
    """HTML "Summary" panel: model sentences (escaped) next to our own numbers.

    ``kind`` is ``"premigration"`` or ``"enhanced"``.
    """
    if kind == "premigration":
        labels = ("Ready to migrate", "Predicted to fail", "What to do next")
        facts = _facts_premigration(aggregates)
    else:
        labels = ("What migrated", "What failed", "What to do next")
        facts = _facts_enhanced(aggregates)
    sentences = (narrative.what_migrated, narrative.what_failed, narrative.what_to_do_next)
    rows = "".join(
        '<div style="padding:10px 0;border-top:1px solid var(--line,#e5e7eb)">'
        f'<div style="font-weight:600;margin-bottom:2px">{escape(label)}</div>'
        f'<div style="font-variant-numeric:tabular-nums;color:var(--ink,#111827)">{fact}</div>'
        f'<p style="margin-top:4px;color:var(--ink-2,#4b5563)">{escape(sentence)}</p>'
        "</div>"
        for label, fact, sentence in zip(labels, facts, sentences, strict=True)
    )
    return (
        '<section id="ai-summary" aria-label="Summary" style="max-width:1200px;margin:16px auto;'
        "padding:16px 20px;background:var(--surface,#fff);color:var(--ink,#111827);"
        'border:1px solid var(--line,#e5e7eb);border-radius:10px;font-size:0.95rem;line-height:1.45">'
        '<div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline">'
        '<h2 style="font-size:1.1rem;margin:0 0 8px">Summary</h2>'
        '<span style="font-size:0.75rem;color:var(--ink-2,#4b5563)">Sentences written by AI; '
        "numbers from the migration data</span></div>"
        f'<p style="font-size:1.05rem;margin-bottom:6px">{escape(narrative.headline)}</p>'
        f"{rows}</section>"
    )

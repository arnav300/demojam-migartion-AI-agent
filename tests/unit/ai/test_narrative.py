"""Unit tests for C9 executive narrative (model stubbed with httpx.MockTransport)."""

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from aap_migration.ai.client import AIClient, AIConfig
from aap_migration.ai.narrative import (
    build_narrative,
    enhanced_aggregates,
    premigration_aggregates,
    render_summary_panel,
)
from aap_migration.ai.schemas import ReportNarrative
from aap_migration.cli.commands import migration_report_v2
from aap_migration.reporting.premigration_html import render_premigration_html

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures"

NARRATIVE = {
    "headline": "Most of the estate can move to AAP 2.6 <now>.",
    "what_migrated": "Projects and inventories are in good shape.",
    "what_failed": "Several job templates lack an inventory (RC-002).",
    "what_to_do_next": "Assign inventories on AAP 2.4, then re-run the report.",
}


def _report() -> dict:
    spec = importlib.util.spec_from_file_location("make_golden", FIXTURES / "make_golden.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_report()


def _org_summary() -> dict:
    def res(rt, sid, status, key=""):
        return {"resource_type": rt, "source_id": sid, "status": status, "error_key": key}

    return {
        "Org A": {"resources": [
            res("projects", 1, "completed"),
            res("job_templates", 2, "failed", "API: Inventory required"),
            res("job_templates", 3, "failed", "API: Playbook not found for project"),
        ]},
        "Org B": {"resources": [
            res("projects", 4, "completed"),
            res("users", 5, "failed", "API: Something new"),
            res("inventories", 6, "skipped", "Inventory: empty (0 hosts)"),
        ]},
        "Org C": {"resources": [res("projects", 7, "completed")]},
    }


class FakeModel:
    def __init__(self, answers):
        self.answers = list(answers)
        self.bodies: list[dict] = []

    def __call__(self, request):
        self.bodies.append(json.loads(request.content))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    def client(self):
        return AIClient(config=AIConfig(endpoint="http://ai.test"), transport=httpx.MockTransport(self))


def test_premigration_aggregates():
    agg = premigration_aggregates(_report())
    assert agg["totals"]["objects"] == 6  # 2 organizations, 1 project, 3 job templates
    assert agg["totals"]["will_fail"] == 3
    assert agg["per_rc"] == {"RC-001": 1, "RC-002": 3, "STALE": 3}
    assert agg["top_failing_orgs"] == [
        {"organization": "Org A", "will_fail": 2, "at_risk": 0},
        {"organization": "Org B", "will_fail": 1, "at_risk": 0},
    ]


def test_enhanced_aggregates():
    agg = enhanced_aggregates(_org_summary())
    assert agg["totals"] == {
        "objects": 7, "organizations": 3, "completed": 3, "failed": 3, "skipped": 1, "pending": 0,
    }
    assert agg["per_rc"] == {"RC-001": 1, "RC-002": 1, "RC-011": 1}
    assert agg["per_type"]["job_templates"] == {"failed": 2}
    assert agg["top_failing_orgs"] == [
        {"organization": "Org A", "failed": 2},
        {"organization": "Org B", "failed": 1},
    ]


def test_ai_off_returns_none(monkeypatch):
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    assert build_narrative({"totals": {}}) is None


def test_prompt_contains_aggregates_only():
    model = FakeModel([NARRATIVE])
    agg = premigration_aggregates(_report())

    narrative = build_narrative(agg, client=model.client())

    assert narrative == ReportNarrative.model_validate(NARRATIVE)
    user = model.bodies[0]["messages"][1]["content"]
    assert json.loads(user.split("\n", 1)[1]) == {"aggregates": json.loads(json.dumps(agg))}
    for name in ("jt-no-inv", "jt-no-proj", "proj-ok"):
        assert name not in user


def test_numbers_from_model_are_rejected():
    with_numbers = {**NARRATIVE, "what_failed": "3 job templates failed."}
    model = FakeModel([with_numbers, with_numbers])
    assert build_narrative({"totals": {}}, client=model.client()) is None
    assert len(model.bodies) == 2  # retried once with the validation error


@pytest.mark.parametrize("text", ["Moves to AAP 2.6 soon.", "Fix RC-002 first.", "From 2.4 to 2.6."])
def test_versions_and_rc_codes_allowed(text):
    ReportNarrative.model_validate({**NARRATIVE, "headline": text})


@pytest.mark.parametrize("text", ["About 40% failed.", "Top five: 5 orgs.", "Version 2.5 here."])
def test_other_numbers_rejected(text):
    with pytest.raises(ValidationError):
        ReportNarrative.model_validate({**NARRATIVE, "headline": text})


def test_panel_numbers_come_from_aggregates_and_text_is_escaped():
    agg = enhanced_aggregates(_org_summary())
    html = render_summary_panel(ReportNarrative.model_validate(NARRATIVE), agg, kind="enhanced")
    assert "3 of 7 objects migrated" in html
    assert "3 failed · 1 skipped · 0 pending" in html
    assert "Org A (2)" in html
    assert "&lt;now&gt;" in html and "<now>" not in html


def test_premigration_html_panel_only_when_narrative_present():
    report = _report()
    golden = (FIXTURES / "premigration_golden.html").read_text()
    assert render_premigration_html(report) == golden

    report["ai_narrative"] = NARRATIVE
    html = render_premigration_html(report)
    assert 'id="ai-summary"' in html
    assert "3 will fail · 0 at risk" in html
    panel = render_summary_panel(
        ReportNarrative.model_validate(NARRATIVE), premigration_aggregates(report), "premigration"
    )
    assert "</header>\n" + panel + "\n" in html


def test_enhanced_html_panel_only_when_present(tmp_path, monkeypatch):
    fixed = SimpleNamespace(now=lambda: datetime(2026, 10, 19, 12, 0, 0))
    monkeypatch.setattr(migration_report_v2, "datetime", fixed)
    org_summary = {
        name: {**s, "total": len(s["resources"]), "resource_types": {r["resource_type"] for r in s["resources"]}}
        for name, s in _org_summary().items()
    }
    state = SimpleNamespace(migration_id="test-run")

    def render(summary_html=""):
        return migration_report_v2._generate_enhanced_html(
            org_summary, state, tmp_path, org_mapper=None, summary_html=summary_html
        )

    plain = render()
    assert 'id="ai-summary"' not in plain
    panel = render_summary_panel(
        ReportNarrative.model_validate(NARRATIVE), enhanced_aggregates(org_summary), "enhanced"
    ) + "\n"
    with_panel = render(panel)
    assert with_panel.index('id="ai-summary"') < with_panel.index('<div class="tabs">')
    assert with_panel.replace(panel, "", 1) == plain


def test_enhanced_panel_helper_ai_off(monkeypatch):
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    assert migration_report_v2._ai_summary_panel(_org_summary(), None) == ""
    assert migration_report_v2._ai_summary_panel(_org_summary(), True) == ""

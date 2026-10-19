"""Unit tests for C7 pre-migration explanations (model stubbed with httpx.MockTransport)."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from aap_migration.ai.client import AIClient, AIConfig
from aap_migration.ai.explain import explain_findings, group_findings
from aap_migration.analysis.premigration import run_analysis
from aap_migration.cli.commands import premigration_report
from aap_migration.reporting.premigration_html import render_premigration_html

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures"


def _golden_module():
    spec = importlib.util.spec_from_file_location("make_golden", FIXTURES / "make_golden.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def result():
    inventory = json.loads((FIXTURES / "premigration_inventory.json").read_text())
    return run_analysis(inventory, source_url="https://aap24.example.com", source_version="2.4")


class FakeModel:
    """Answers with a valid explanation for the rc_code found in the prompt facts."""

    def __init__(self, rc_override: str | None = None):
        self.requests: list[dict] = []
        self.rc_override = rc_override

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        facts = json.loads(body["messages"][1]["content"].split("\n", 1)[1])
        answer = {
            "rc_code": self.rc_override or facts["rc_code"],
            "summary": f"{facts['affected_count']} {facts['resource_type']} affected.",
            "why_it_fails": "AAP 2.6 rejects them.",
            "operator_action": "Fix them on AAP 2.4 first.",
            "confidence": "medium",
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    def client(self) -> AIClient:
        return AIClient(
            config=AIConfig(endpoint="http://ai.test", model="granite"),
            transport=httpx.MockTransport(self),
        )


def test_grouping_by_rc_and_type(result):
    groups = group_findings(result.findings)
    assert {k: len(v) for k, v in groups.items()} == {
        ("RC-001", "job_templates"): 1,
        ("RC-002", "job_templates"): 3,
        ("STALE", "job_templates"): 3,
    }


def test_one_call_per_group_names_only(result, tmp_path):
    model = FakeModel()
    explanations = explain_findings(result, client=model.client(), cache_path=tmp_path / "c.json")

    assert set(explanations) == {"RC-001", "RC-002", "STALE"}
    assert explanations["RC-002"].summary == "3 job_templates affected."
    assert len(model.requests) == 3
    facts = [json.loads(r["messages"][1]["content"].split("\n", 1)[1]) for r in model.requests]
    rc002 = next(f for f in facts if f["rc_code"] == "RC-002")
    assert rc002["sample_resource_names"] == ["jt-b-no-inv", "jt-no-inv", "jt-no-proj"]
    assert set(rc002) == {
        "rc_code", "rc_title", "rc_description", "rc_remediation",
        "resource_type", "affected_count", "sample_resource_names",
    }


def test_samples_capped_at_five(result):
    finding = result.findings[0]
    result.findings = [
        type(finding)(**{**finding.__dict__, "resource_name": f"jt-{i}", "resource_id": i})
        for i in range(8)
    ]
    model = FakeModel()
    explain_findings(result, client=model.client())
    facts = json.loads(model.requests[0]["messages"][1]["content"].split("\n", 1)[1])
    assert facts["sample_resource_names"] == ["jt-0", "jt-1", "jt-2", "jt-3", "jt-4"]
    assert facts["affected_count"] == 8


def test_cache_hit_avoids_call(result, tmp_path):
    cache = tmp_path / "ai_cache.json"
    first = explain_findings(result, client=FakeModel().client(), cache_path=cache)
    assert cache.is_file()

    model = FakeModel()
    second = explain_findings(result, client=model.client(), cache_path=cache)

    assert model.requests == []
    assert second == first


def test_model_changing_rc_code_is_dropped(result, tmp_path):
    model = FakeModel(rc_override="RC-005")
    assert explain_findings(result, client=model.client(), cache_path=tmp_path / "c.json") == {}
    assert not (tmp_path / "c.json").exists()  # nothing invalid is cached
    assert len(model.requests) == 6  # one corrective retry per group


def test_failed_calls_are_skipped(result):
    client = AIClient(
        config=AIConfig(endpoint="http://ai.test"),
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
    )
    assert explain_findings(result, client=client) == {}


def test_ai_off_makes_no_call(result, monkeypatch):
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    assert explain_findings(result) == {}


def test_ai_off_html_is_byte_identical_to_golden():
    report = _golden_module().build_report()
    golden = (FIXTURES / "premigration_golden.html").read_text()
    assert render_premigration_html(report) == golden


def test_ai_on_html_adds_explanations():
    report = _golden_module().build_report()
    report["ai_explanations"] = {
        "RC-002": {
            "rc_code": "RC-002",
            "summary": "<script>alert(1)</script>",
            "why_it_fails": "w",
            "operator_action": "a",
            "confidence": "high",
        }
    }
    html = render_premigration_html(report)
    assert "AI explanations (optional)" in html
    assert "esc(e.summary)" in html
    assert "<script>alert(1)</script>" not in html  # embedded JSON is escaped


def test_resolve_ai_flag(monkeypatch):
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    assert [premigration_report.resolve_ai(f) for f in (None, True, False)] == [False] * 3
    monkeypatch.setenv("AI_ENDPOINT", "http://ai.test")
    assert [premigration_report.resolve_ai(f) for f in (None, True, False)] == [True, True, False]


def test_command_helper_scopes_findings_to_org(result, tmp_path, monkeypatch):
    seen = []

    def fake_explain(res, cache_path):
        seen.append({f.organization for f in res.findings})
        return {}

    monkeypatch.setattr(premigration_report, "explain_findings", fake_explain)

    report = {"organizations": {"Org B": {}}}
    premigration_report._add_ai_explanations(report, result, tmp_path, scoped=True)
    assert seen == [{"Org B"}]
    assert "ai_explanations" not in report

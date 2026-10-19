"""Unit tests for 'Predicted vs actual' (rules only)."""

import importlib.util
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from aap_migration.analysis.prediction_check import (
    compare_predictions,
    load_actual_rows,
    render_prediction_panel,
)
from aap_migration.cli import menu
from aap_migration.cli.commands import migration_report_v2

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "ai" / "fixtures"


def _report() -> dict:
    """Golden pre-migration report: jt 10, 11, 12 predicted to fail (RC-001 on 11, RC-002 on all)."""
    spec = importlib.util.spec_from_file_location("make_golden", FIXTURES / "make_golden.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_report()


def _row(rtype, sid, status, error=None, name=""):
    return {"resource_type": rtype, "source_id": sid, "source_name": name or f"{rtype}-{sid}",
            "status": status, "error_message": error}


ROWS = [
    _row("job_templates", 10, "failed", "API error: inventory: This field may not be null."),
    _row("job_templates", 11, "failed", "APIError: [400] Playbook not found for project 'p'"),
    _row("job_templates", 12, "completed"),
    _row("projects", 5, "failed", "Something odd happened.", name="<b>proj</b>"),
    _row("organizations", 1, "completed"),
]


def test_compare_categories_and_metrics():
    result = compare_predictions(_report(), ROWS)
    assert result["totals"] == {
        "predicted_failures": 3, "actual_failures": 3, "confirmed": 2, "confirmed_same_rc": 2,
        "missed": 1, "false_alarm": 1, "not_migrated": 0,
    }
    assert result["metrics"] == {"recall_pct": 66.7, "precision_pct": 66.7, "rc_agreement_pct": 100.0}
    by_id = {(r["resource_type"], r["source_id"]): r for r in result["resources"]}
    assert by_id[("job_templates", 10)]["category"] == "confirmed"
    assert by_id[("job_templates", 10)]["actual_rc"] == "RC-002"
    assert by_id[("job_templates", 11)]["predicted_rc"] == ["RC-001", "RC-002"]
    assert by_id[("job_templates", 12)]["category"] == "false_alarm"
    assert by_id[("projects", 5)]["category"] == "missed"
    assert ("organizations", 1) not in by_id  # predicted fine and fine: not listed
    assert result["per_rc"]["RC-002"] == {"predicted": 3, "failed": 1, "confirmed": 1}
    assert list(result["per_rc"])[-1] == "UNCLASSIFIED"


def test_rc_mismatch_counts_as_confirmed_but_not_same_rc():
    rows = [_row("job_templates", 10, "failed", "Duplicate exists in target")]
    result = compare_predictions(_report(), rows)
    assert result["totals"]["confirmed"] == 1
    assert result["totals"]["confirmed_same_rc"] == 0
    assert result["metrics"]["rc_agreement_pct"] == 0.0


def test_not_migrated_and_org_filter():
    result = compare_predictions(_report(), ROWS[:2])
    assert result["totals"]["not_migrated"] == 1  # jt 12 never imported
    scoped = compare_predictions(_report(), [], organizations={"org b"})
    assert scoped["totals"]["predicted_failures"] == 1


def test_no_failures_gives_empty_metrics():
    result = compare_predictions({"findings": [], "resources": {}}, [_row("projects", 1, "completed")])
    assert result["metrics"] == {"recall_pct": None, "precision_pct": None, "rc_agreement_pct": None}


def test_load_actual_rows_reads_db_unchanged():
    db = FIXTURES / "failures.db"
    before = db.read_bytes()
    rows = load_actual_rows(str(db))
    assert len(rows) == 14
    assert db.read_bytes() == before


def test_panel_numbers_and_escaping():
    html = render_prediction_panel(compare_predictions(_report(), ROWS))
    assert "flagged 2 of the 3 objects that failed, 2 with the same root cause." in html
    assert "66.7%" in html
    assert "&lt;b&gt;proj&lt;/b&gt;" in html and "<b>proj</b>" not in html


def test_enhanced_report_panel(tmp_path):
    prediction = tmp_path / "premigration-report.json"
    prediction.write_text(json.dumps(_report()))
    org_summary = {"Org A": {"resources": ROWS}}
    html = migration_report_v2._prediction_panel(prediction, org_summary, organization=None)
    assert 'id="prediction-check"' in html


def test_cli(tmp_path, monkeypatch):
    from aap_migration.cli.main import cli

    monkeypatch.chdir(tmp_path)
    prediction = tmp_path / "premigration-report.json"
    prediction.write_text(json.dumps(_report()))
    result = CliRunner().invoke(cli, [
        "prediction-check", "--prediction", str(prediction),
        "--db", str(FIXTURES / "failures.db"), "--output-dir", "out",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "out" / "prediction-check.json").read_text())
    assert data["totals"]["actual_failures"] == 13  # includes the failed row without a message


def test_menu_option(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "premigration-report.json").write_text("{}")
    answers = iter(["5", "", "1", "", "b"])
    commands = []
    monkeypatch.setattr(menu.Prompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(menu, "run_command", lambda args, ctx=None: commands.append(args))
    monkeypatch.setattr(menu.Console, "clear", lambda self: None)
    menu.post_migration_submenu(None)
    assert commands == [
        ["prediction-check", "--prediction", "reports/premigration-report.json"],
        ["enhanced-report", "--prediction", "reports/premigration-report.json"],
    ]

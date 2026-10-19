"""Unit tests for C8 failure classification (model stubbed with httpx.MockTransport)."""

import csv
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from aap_migration.ai.classify import classify_failures, load_failed_rows
from aap_migration.ai.client import AIClient, AIConfig
from aap_migration.ai.rc_map import ERROR_KEY_TO_RC, UNCLASSIFIED, rc_for_error
from aap_migration.ai.schemas import RC_CODES

pytestmark = pytest.mark.unit

DB = Path(__file__).parent / "fixtures" / "failures.db"


class FakeModel:
    def __init__(self, rc_override: str | None = None):
        self.facts: list[dict] = []
        self.rc_override = rc_override

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        facts = json.loads(body["messages"][1]["content"].split("\n", 1)[1])
        self.facts.append(facts)
        answer = {
            "rc_code": self.rc_override or facts["rc_code"],
            "explanation": f"Explains {facts['error_key']}",
            "confidence": "high",
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    def client(self) -> AIClient:
        return AIClient(
            config=AIConfig(endpoint="http://ai.test"), transport=httpx.MockTransport(self)
        )


@pytest.mark.parametrize(
    ("key", "resource_type", "expected"),
    [
        ("API: Playbook not found for project", "job_templates", "RC-001"),
        ("API: variables_needed_to_start (region, env)", "schedules", "RC-003"),
        ("Duplicate credential", "credentials", "RC-008"),
        ("Validation: Field '...' was removed", "projects", "RC-010"),
        ("API: Field required (organization)", "projects", "RC-010"),
        ("API: Something new", "users", "RC-011"),
        ("API: Something new", "projects", UNCLASSIFIED),
        ("", "projects", UNCLASSIFIED),
    ],
)
def test_rc_for_error(key, resource_type, expected):
    assert rc_for_error(key, resource_type) == expected


def test_table_only_maps_to_known_codes():
    assert set(ERROR_KEY_TO_RC.values()) <= set(RC_CODES)


def test_load_failed_rows_skips_completed_and_empty():
    rows = load_failed_rows(DB)
    names = {r.source_name for r in rows}
    assert len(rows) == 12
    assert "proj-ok" not in names and "proj-empty" not in names


def test_state_db_is_only_read():
    before = hashlib.sha256(DB.read_bytes()).hexdigest()
    classify_failures(load_failed_rows(DB), use_ai=False)
    assert hashlib.sha256(DB.read_bytes()).hexdigest() == before


def test_every_rc_code_and_unknown_classified_ai_off(monkeypatch):
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    result = classify_failures(load_failed_rows(DB))

    assert result.ai_used is False
    assert result.per_rc == {rc: 1 for rc in (*RC_CODES, UNCLASSIFIED)}
    assert all(r.ai_explanation is None for r in result.resources)
    by_name = {r.source_name: r.rc_code for r in result.resources}
    assert by_name["proj-weird"] == UNCLASSIFIED
    assert by_name["ldap-user"] == "RC-011"


def test_ai_one_call_per_group_and_explanations_attached():
    rows = load_failed_rows(DB)
    rows.append(type(rows[0])("job_templates", 104, "jt-playbook-2", rows[0].error_message))
    model = FakeModel()

    result = classify_failures(rows, client=model.client())

    assert result.ai_used is True
    assert len(model.facts) == len(result.groups) == 12
    group = next(g for g in result.groups if g.rc_code == rows[0].rc_code)
    assert group.count == 2
    assert group.ai_confidence == "high"
    assert all(r.ai_explanation for r in result.resources)
    sent = next(f for f in model.facts if f["rc_code"] == group.rc_code)
    assert sent["sample_resource_names"] == sorted(group.sample_names)
    assert "error_message" not in sent  # only the normalised key is sent


def test_model_cannot_change_rc_code():
    result = classify_failures(load_failed_rows(DB), client=FakeModel(rc_override="RC-001").client())

    assert result.per_rc == {rc: 1 for rc in (*RC_CODES, UNCLASSIFIED)}
    explained = {g.rc_code for g in result.groups if g.ai_explanation}
    assert explained == {"RC-001"}


def test_no_ai_flag_makes_no_call():
    model = FakeModel()
    result = classify_failures(load_failed_rows(DB), client=model.client(), use_ai=False)
    assert model.facts == [] and result.ai_used is False


def test_cli_writes_json_and_csv(tmp_path, monkeypatch):
    from aap_migration.cli.main import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_ENDPOINT", "http://ai.invalid")  # must not be called with --no-ai
    out = tmp_path / "out"
    result = CliRunner().invoke(
        cli, ["classify-failures", "--db", str(DB), "--output-dir", str(out), "--no-ai"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads((out / "classification.json").read_text())
    assert data["total_failed"] == 12
    assert data["ai_used"] is False
    assert data["per_rc"]["UNCLASSIFIED"] == 1
    with open(out / "classification.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 12
    assert {r["rc_code"] for r in rows} == {*RC_CODES, UNCLASSIFIED}


def test_cli_needs_db_or_config(tmp_path, monkeypatch):
    from aap_migration.cli.main import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AAP_BRIDGE_CONFIG", raising=False)
    result = CliRunner().invoke(cli, ["classify-failures"])
    assert result.exit_code == 2
    assert "Pass --db or --config" in result.output

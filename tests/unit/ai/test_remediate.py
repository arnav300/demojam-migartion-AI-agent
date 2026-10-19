"""Unit tests for C10 remediation playbooks (model and ansible tools stubbed)."""

import json
import subprocess
from pathlib import Path

import httpx
import pytest
import yaml
from aap_migration.ai.client import AIClient, AIConfig
from aap_migration.ai.remediate import build_remediation, load_rc_inputs, static_problems
from click.testing import CliRunner

pytestmark = pytest.mark.unit

CLASSIFICATION = {
    "groups": [
        {"rc_code": "RC-002", "error_key": "API: Inventory required", "count": 2,
         "sample_names": ["jt-b", "jt-a"]},
        {"rc_code": "RC-002", "error_key": "API: Cannot enable provisioning callback without inventory",
         "count": 1, "sample_names": ["jt-c"]},
        {"rc_code": "RC-001", "error_key": "API: Playbook not found for project", "count": 1,
         "sample_names": ["jt-p"]},
        {"rc_code": "UNCLASSIFIED", "error_key": "Something", "count": 1, "sample_names": ["x"]},
    ]
}

VALID_PLAYBOOK = """\
- name: Assign an inventory to job templates
  hosts: localhost
  connection: local
  gather_facts: false
  check_mode: "{{ remediation_check_mode | default(true) }}"
  tasks:
    - name: Set inventory
      ansible.controller.job_template:
        name: "{{ item }}"
        inventory: Demo Inventory
      loop: "{{ remediation_targets }}"
"""


def _model(playbook: str = VALID_PLAYBOOK, rc_override: str | None = None):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        facts = json.loads(body["messages"][1]["content"].split("\n", 1)[1])
        calls.append(facts)
        answer = {
            "rc_code": rc_override or facts["rc_code"],
            "title": f"Fix {facts['rc_code']}",
            "playbook_yaml": playbook,
            "assumptions": ["Inventory 'Demo Inventory' exists"],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    client = AIClient(config=AIConfig(endpoint="http://ai.test"), transport=httpx.MockTransport(handler))
    return client, calls


class FakeTools:
    def __init__(self, installed=("ansible-playbook", "ansible-lint"), syntax_rc=0, lint_rc=0):
        self.installed = set(installed)
        self.syntax_rc = syntax_rc
        self.lint_rc = lint_rc
        self.commands: list[list[str]] = []

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.installed else None

    def run(self, cmd, cwd: Path):
        self.commands.append(cmd)
        assert (cwd / cmd[-1]).is_file()  # the draft is checked from a temp dir
        rc = self.syntax_rc if "--syntax-check" in cmd else self.lint_rc
        return subprocess.CompletedProcess(cmd, rc, stdout="lint output", stderr="syntax error")


@pytest.fixture
def templates(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "RC-001.yml").write_text("# template RC-001\n- hosts: localhost\n  tasks: []\n")
    (d / "RC-002.yml").write_text("# template RC-002\n- hosts: localhost\n  tasks: []\n")
    return d


def _build(tmp_path, templates, client=None, tools=None, use_ai=None):
    tools = tools or FakeTools()
    out = tmp_path / "out"
    entries = build_remediation(
        CLASSIFICATION, out, templates, client=client, use_ai=use_ai, runner=tools.run, which=tools.which
    )
    return {e.rc_code: e for e in entries}, out, tools


def test_rc_inputs_one_per_rc_code():
    inputs = {i.rc_code: i for i in load_rc_inputs(CLASSIFICATION)}
    assert set(inputs) == {"RC-001", "RC-002"}  # UNCLASSIFIED has no playbook
    assert inputs["RC-002"].error_key == "API: Inventory required"
    assert inputs["RC-002"].sample_names == ["jt-a", "jt-b", "jt-c"]
    assert inputs["RC-002"].count == 3


def test_generated_valid_path(tmp_path, templates):
    client, calls = _model()
    entries, out, tools = _build(tmp_path, templates, client=client)

    assert {e.source for e in entries.values()} == {"generated"}
    assert len(calls) == 2
    assert calls[1]["sample_resource_names"] == ["jt-a", "jt-b", "jt-c"]
    text = (out / "RC-002.yml").read_text()
    assert "ansible.controller.job_template" in text and "Drafted by AI" in text
    variables = yaml.safe_load((out / "RC-002.vars.yml").read_text())
    assert variables == {"remediation_check_mode": True, "remediation_targets": ["jt-a", "jt-b", "jt-c"]}
    assert [c[:2] for c in tools.commands[:2]] == [["ansible-playbook", "--syntax-check"], ["ansible-lint", "--offline"]]
    readme = (out / "README.md").read_text()
    assert "| RC-002 | Job template without inventory | generated |" in readme


def test_generated_invalid_syntax_falls_back_to_template(tmp_path, templates):
    client, _ = _model()
    entries, out, _ = _build(tmp_path, templates, client=client, tools=FakeTools(syntax_rc=4))

    assert entries["RC-002"].source == "template"
    assert "syntax-check failed" in entries["RC-002"].reason
    assert (out / "RC-002.yml").read_text().startswith("# template RC-002")
    assert "| RC-002 | Job template without inventory | template |" in (out / "README.md").read_text()


def test_lint_failure_falls_back_to_template(tmp_path, templates):
    client, _ = _model()
    entries, _, _ = _build(tmp_path, templates, client=client, tools=FakeTools(lint_rc=2))
    assert entries["RC-001"].source == "template"
    assert "ansible-lint failed" in entries["RC-001"].reason


def test_lint_not_installed_is_skipped(tmp_path, templates):
    client, _ = _model()
    tools = FakeTools(installed=("ansible-playbook",))
    entries, _, tools = _build(tmp_path, templates, client=client, tools=tools)
    assert {e.source for e in entries.values()} == {"generated"}
    assert all(c[0] == "ansible-playbook" for c in tools.commands)


def test_without_ansible_playbook_uses_template(tmp_path, templates):
    client, _ = _model()
    entries, _, _ = _build(tmp_path, templates, client=client, tools=FakeTools(installed=()))
    assert {e.source for e in entries.values()} == {"template"}
    assert "not installed" in entries["RC-001"].reason


@pytest.mark.parametrize(
    ("playbook", "problem"),
    [
        (VALID_PLAYBOOK.replace("ansible.controller.job_template", "awx.awx.job_template"), "awx.awx"),
        (VALID_PLAYBOOK.replace("hosts: localhost", "hosts: all"), "localhost"),
        (VALID_PLAYBOOK.replace('  check_mode: "{{ remediation_check_mode | default(true) }}"\n', ""), "check_mode"),
        ("not: [valid", "invalid YAML"),
    ],
)
def test_static_checks_reject_unsafe_drafts(tmp_path, templates, playbook, problem):
    assert any(problem in p for p in static_problems(playbook))
    client, _ = _model(playbook)
    entries, _, tools = _build(tmp_path, templates, client=client)
    assert {e.source for e in entries.values()} == {"template"}
    assert tools.commands == []  # rejected before any tool runs


def test_static_checks_accept_valid_and_blocks():
    assert static_problems(VALID_PLAYBOOK) == []
    blocky = VALID_PLAYBOOK.replace(
        "  tasks:\n", "  tasks:\n    - block:\n        - ansible.builtin.debug:\n            msg: hi\n"
    )
    assert static_problems(blocky) == []


def test_model_changing_rc_code_uses_template(tmp_path, templates):
    client, _ = _model(rc_override="RC-009")
    entries, _, _ = _build(tmp_path, templates, client=client)
    assert {e.source for e in entries.values()} == {"template"}


def test_ai_off_uses_templates(tmp_path, templates, monkeypatch):
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    entries, out, tools = _build(tmp_path, templates)

    assert {e.source for e in entries.values()} == {"template"}
    assert tools.commands == []
    assert (out / "RC-001.yml").read_text().startswith("# template RC-001")
    assert yaml.safe_load((out / "RC-001.vars.yml").read_text())["remediation_check_mode"] is True


def test_no_ai_flag_makes_no_call(tmp_path, templates):
    client, calls = _model()
    entries, _, _ = _build(tmp_path, templates, client=client, use_ai=False)
    assert calls == []
    assert {e.source for e in entries.values()} == {"template"}


def test_missing_template_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    empty = tmp_path / "none"
    entries, out, _ = _build(tmp_path, empty)
    assert {e.source for e in entries.values()} == {"missing"}
    assert not (out / "RC-001.yml").exists()
    assert "| RC-001 | Missing or broken project / playbook | missing |" in (out / "README.md").read_text()


def test_cli_ai_off(tmp_path, templates, monkeypatch):
    from aap_migration.cli.main import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    (tmp_path / "classification.json").write_text(json.dumps(CLASSIFICATION))
    result = CliRunner().invoke(
        cli,
        ["remediate", "--from", "classification.json", "--out", "rem", "--templates", str(templates)],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "rem" / "README.md").is_file()
    assert (tmp_path / "rem" / "RC-002.yml").read_text().startswith("# template RC-002")


def test_validation_env_puts_shim_last(monkeypatch):
    from aap_migration.ai.remediate import VALIDATION_COLLECTIONS, validation_env

    monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/real/collections")
    assert validation_env()["ANSIBLE_COLLECTIONS_PATH"] == f"/real/collections:{VALIDATION_COLLECTIONS}"
    monkeypatch.delenv("ANSIBLE_COLLECTIONS_PATH")
    path = validation_env()["ANSIBLE_COLLECTIONS_PATH"]
    assert path.startswith("~/.ansible/collections") and path.endswith(str(VALIDATION_COLLECTIONS))


def test_shim_routes_controller_modules_to_awx():
    from aap_migration.ai.remediate import VALIDATION_COLLECTIONS

    runtime = VALIDATION_COLLECTIONS / "ansible_collections" / "ansible" / "controller" / "meta" / "runtime.yml"
    routing = yaml.safe_load(runtime.read_text())["plugin_routing"]["modules"]
    assert routing["job_template"] == {"redirect": "awx.awx.job_template"}
    assert all(v["redirect"] == f"awx.awx.{k}" for k, v in routing.items())


def _awx_available() -> bool:
    import os
    import shutil as sh

    if not sh.which("ansible-playbook"):
        return False
    paths = os.environ.get("ANSIBLE_COLLECTIONS_PATH", "~/.ansible/collections").split(":")
    return any((Path(p).expanduser() / "ansible_collections" / "awx" / "awx").is_dir() for p in paths)


@pytest.mark.skipif(not _awx_available(), reason="needs ansible-core and awx.awx (present in the image)")
def test_real_syntax_check_with_shim(tmp_path):
    from aap_migration.ai.remediate import tool_checks

    playbook, vars_file = tmp_path / "RC-002.yml", tmp_path / "RC-002.vars.yml"
    vars_file.write_text("remediation_check_mode: true\nremediation_targets: [a]\n")
    playbook.write_text(VALID_PLAYBOOK)
    assert tool_checks(playbook, vars_file, which=lambda n: n == "ansible-playbook" or None) == []
    playbook.write_text(VALID_PLAYBOOK.replace("job_template", "no_such_module"))
    problems = tool_checks(playbook, vars_file, which=lambda n: n == "ansible-playbook" or None)
    assert problems and "syntax-check failed" in problems[0]

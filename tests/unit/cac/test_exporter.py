"""Unit tests for T19 configuration-as-code export (infra.aap_configuration format)."""

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from aap_migration.cac.exporter import VAR_NAMES, export_organization, write_cac

pytestmark = pytest.mark.unit

MACHINE_FIELDS = [
    {"id": "username", "label": "Username", "type": "string"},
    {"id": "password", "label": "Password", "type": "string", "secret": True},
    {"id": "ssh_key_data", "label": "Key", "type": "string", "secret": True, "multiline": True},
]

EXPORT = {
    "organizations": [
        {"id": 1, "name": "Finance", "description": "Fin", "max_hosts": 0, "default_environment": 7},
        {"id": 2, "name": "Ops"},
    ],
    "execution_environments": [{"id": 7, "name": "EE Prod"}],
    "teams": [
        {"id": 1, "name": "fin-admins", "organization": 1},
        {"id": 2, "name": "ops-team", "organization": 2},
    ],
    "credential_types": [
        {"id": 1, "name": "Machine", "managed": True, "kind": "ssh", "inputs": {"fields": MACHINE_FIELDS}},
        {"id": 20, "name": "Custom API", "managed": False, "kind": "cloud",
         "inputs": {"fields": [{"id": "api_key", "label": "Key", "type": "string", "secret": True}]},
         "injectors": {"env": {"API_KEY": "{{ api_key }}"}}},
    ],
    "credentials": [
        {"id": 1, "name": "fin-ssh", "organization": 1, "credential_type": 1,
         "inputs": {"username": "svc", "password": "$encrypted$", "ssh_key_data": "-----BEGIN REAL KEY-----"}},
        {"id": 2, "name": "global-scm", "organization": None, "credential_type": 1,
         "inputs": {"username": "git", "password": "$encrypted$"}},
        {"id": 3, "name": "fin-api", "organization": 1, "credential_type": 20,
         "inputs": {"api_key": "plaintext-real-secret"}},
        {"id": 4, "name": "ops-ssh", "organization": 2, "credential_type": 1, "inputs": {}},
        {"id": 5, "name": "unused-global", "organization": None, "credential_type": 1, "inputs": {}},
    ],
    "projects": [
        {"id": 10, "name": "fin-playbooks", "organization": 1, "scm_type": "git",
         "scm_url": "https://git.example.com/fin.git", "scm_branch": "main", "credential": 2,
         "scm_update_on_launch": True, "default_environment": None},
        {"id": 11, "name": "ops-project", "organization": 2, "scm_type": "git"},
    ],
    "inventories": [
        {"id": 30, "name": "fin-inv", "organization": 1, "kind": "",
         "variables": "---\nenv: prod\nhome: '{{ lookup(\"env\", \"HOME\") }}'\n"},
        {"id": 31, "name": "ops-inv", "organization": 2},
    ],
    "inventory_sources": [
        {"id": 40, "name": "fin-scm-src", "inventory": 30, "source": "scm", "source_project": 10,
         "source_path": "inv/hosts", "overwrite": True, "credential": None},
    ],
    "hosts": [
        {"id": 50, "name": "web1", "inventory": 30, "enabled": True, "variables": '{"ansible_host": "10.0.0.5"}'},
        {"id": 51, "name": "db1", "inventory": 30, "enabled": False, "variables": ""},
        {"id": 52, "name": "ops1", "inventory": 31},
    ],
    "inventory_groups": [
        {"id": 60, "name": "web", "inventory": 30, "children": [61]},
        {"id": 61, "name": "web-eu", "inventory": 30},
    ],
    "host_group_memberships": [
        {"group_id": 60, "host_id": 50, "host_name": "web1", "group_name": "web", "inventory_id": 30},
    ],
    "job_templates": [
        {"id": 70, "name": "fin-deploy", "organization": 1, "project": 10, "inventory": 30,
         "playbook": "deploy.yml", "job_type": "run", "execution_environment": 7, "_credentials": [1, 3],
         "summary_fields": {"labels": {"results": [{"id": 1, "name": "prod"}]}},
         "extra_vars": "target: '{{ inventory_hostname }}'", "survey_enabled": True,
         "survey_spec": {"name": "", "description": "", "spec": [
             {"variable": "db_pass", "type": "password", "default": "$encrypted$",
              "question_name": "Password", "required": True}]}},
        {"id": 71, "name": "ops-jt", "organization": 2, "project": 11},
    ],
    "workflow_job_templates": [
        {"id": 80, "name": "fin-wf", "organization": 1, "nodes": [
            {"id": 801, "identifier": "deploy", "unified_job_template": 70,
             "summary_fields": {"unified_job_template": {"name": "fin-deploy", "unified_job_type": "job"}},
             "success_nodes": [802], "failure_nodes": [], "always_nodes": [],
             "all_parents_must_converge": False},
            {"id": 802, "identifier": None, "unified_job_template": 99,
             "summary_fields": {"unified_job_template": {
                 "name": "Approve", "unified_job_type": "workflow_approval",
                 "description": "ok?", "timeout": 600}},
             "success_nodes": [], "failure_nodes": [], "always_nodes": []},
        ]},
    ],
    "schedules": [
        {"id": 90, "name": "nightly", "unified_job_template": 70, "rrule": "DTSTART:20260101T000000Z RRULE:FREQ=DAILY",
         "enabled": True, "summary_fields": {"unified_job_template": {"name": "fin-deploy", "unified_job_type": "job"}}},
        {"id": 91, "name": "ops-nightly", "unified_job_template": 71, "rrule": "x",
         "summary_fields": {"unified_job_template": {"name": "ops-jt", "unified_job_type": "job"}}},
    ],
}

SECRETS = ("plaintext-real-secret", "BEGIN REAL KEY", "$encrypted$")


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!unsafe", lambda loader, node: ("UNSAFE", loader.construct_scalar(node)))


@pytest.fixture
def export_dir(tmp_path):
    root = tmp_path / "exports"
    for rtype, items in EXPORT.items():
        (root / rtype).mkdir(parents=True)
        (root / rtype / f"{rtype}_0001.json").write_text(json.dumps(items))
    return root


@pytest.fixture
def result(export_dir):
    return export_organization(export_dir, "Finance")


def _by_name(result, var):
    return {o["name"]: o for o in result.variables[var]}


def test_only_the_requested_org(result):
    text = json.dumps(result.variables)
    for other in ("Ops", "ops-team", "ops-ssh", "ops-project", "ops-inv", "ops1", "ops-jt", "ops-nightly", "unused-global"):
        assert other not in text
    assert result.counts() == {
        "aap_organizations": 1, "aap_teams": 1, "controller_credential_types": 1,
        "controller_credentials": 3, "controller_projects": 1, "controller_inventories": 1,
        "controller_inventory_sources": 1, "controller_hosts": 2, "controller_groups": 2,
        "controller_templates": 1, "controller_workflows": 1, "controller_schedules": 1,
    }


def test_names_not_ids(result):
    def walk(obj):
        if isinstance(obj, dict):
            assert not isinstance(obj.get("id"), int), obj  # numeric IDs never leak
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(result.variables)
    assert result.variables["aap_organizations"] == [
        {"name": "Finance", "description": "Fin", "default_environment": "EE Prod"}
    ]
    jt = _by_name(result, "controller_templates")["fin-deploy"]
    assert (jt["project"], jt["inventory"], jt["execution_environment"]) == ("fin-playbooks", "fin-inv", "EE Prod")
    assert jt["credentials"] == ["fin-api", "fin-ssh"]
    assert jt["labels"] == ["prod"]
    assert _by_name(result, "controller_projects")["fin-playbooks"]["credential"] == "global-scm"
    src = _by_name(result, "controller_inventory_sources")["fin-scm-src"]
    assert (src["inventory"], src["source_project"]) == ("fin-inv", "fin-playbooks")


def test_global_credential_used_by_org_is_included_without_org(result):
    creds = _by_name(result, "controller_credentials")
    assert "organization" not in creds["global-scm"]
    assert creds["fin-ssh"]["organization"] == "Finance"
    assert creds["fin-ssh"]["credential_type"] == "Machine"


def test_secrets_become_vault_placeholders(result):
    creds = _by_name(result, "controller_credentials")
    assert creds["fin-ssh"]["inputs"] == {
        "username": "svc",
        "password": "{{ vault_fin_ssh_password }}",
        "ssh_key_data": "{{ vault_fin_ssh_ssh_key_data }}",
    }
    assert creds["fin-api"]["inputs"] == {"api_key": "{{ vault_fin_api_api_key }}"}  # secret by type
    jt = _by_name(result, "controller_templates")["fin-deploy"]
    assert jt["survey_spec"]["spec"][0]["default"] == "{{ vault_jt_fin_deploy_db_pass }}"
    assert set(result.vault_vars) == {
        "vault_fin_ssh_password", "vault_fin_ssh_ssh_key_data", "vault_global_scm_password",
        "vault_fin_api_api_key", "vault_jt_fin_deploy_db_pass",
    }


def test_only_custom_credential_types(result):
    types = result.variables["controller_credential_types"]
    assert [t["name"] for t in types] == ["Custom API"]


def test_variables_parsed_and_jinja_marked_unsafe(result, tmp_path):
    inv = _by_name(result, "controller_inventories")["fin-inv"]
    assert inv["variables"]["env"] == "prod"
    hosts = _by_name(result, "controller_hosts")
    assert hosts["web1"]["variables"] == {"ansible_host": "10.0.0.5"}
    assert "variables" not in hosts["db1"]

    out = tmp_path / "cac"
    write_cac(result, out)
    inv_yaml = yaml.load((out / "finance" / "controller_inventories.yml").read_text(), Loader=_Loader)
    assert inv_yaml["controller_inventories"][0]["variables"]["home"][0] == "UNSAFE"
    jt_yaml = yaml.load((out / "finance" / "controller_templates.yml").read_text(), Loader=_Loader)
    jt = jt_yaml["controller_templates"][0]
    assert jt["extra_vars"]["target"][0] == "UNSAFE"
    assert jt["survey_spec"]["spec"][0]["default"] == "{{ vault_jt_fin_deploy_db_pass }}"  # templated, not unsafe
    types = yaml.load((out / "finance" / "controller_credential_types.yml").read_text(), Loader=_Loader)
    assert types["controller_credential_types"][0]["injectors"]["env"]["API_KEY"][0] == "UNSAFE"


def test_groups_hosts_and_children(result):
    groups = _by_name(result, "controller_groups")
    assert groups["web"]["hosts"] == ["web1"]
    assert groups["web"]["children"] == ["web-eu"]
    assert groups["web"]["inventory"] == "fin-inv"


def test_workflow_nodes_by_identifier(result):
    wf = _by_name(result, "controller_workflows")["fin-wf"]
    nodes = {n["identifier"]: n for n in wf["simplified_workflow_nodes"]}
    assert nodes["deploy"]["unified_job_template"] == "fin-deploy"
    assert nodes["deploy"]["type"] == "job_template"
    assert nodes["deploy"]["success_nodes"] == ["node802"]
    assert nodes["node802"]["approval_node"] == {"name": "Approve", "description": "ok?", "timeout": 600}


def test_schedules_scoped_to_org_templates(result):
    schedules = result.variables["controller_schedules"]
    assert [(s["name"], s["unified_job_template"]) for s in schedules] == [("nightly", "fin-deploy")]


def test_written_files_hold_no_secrets(result, tmp_path):
    out = tmp_path / "cac"
    written = write_cac(result, out)
    names = {p.name for p in written}
    assert names == {f"{v}.yml" for v in VAR_NAMES} | {"vault_template.yml", "README.md"}
    for path in written:
        text = path.read_text()
        for secret in SECRETS:
            assert secret not in text, f"{secret} leaked into {path.name}"
    vault = yaml.safe_load((out / "finance" / "vault_template.yml").read_text())
    assert vault == {v: "" for v in result.vault_vars}
    loaded = yaml.load((out / "finance" / "controller_credentials.yml").read_text(), Loader=_Loader)
    assert [c["name"] for c in loaded["controller_credentials"]] == ["fin-api", "fin-ssh", "global-scm"]


def test_unknown_org(export_dir):
    with pytest.raises(ValueError, match="Available: Finance, Ops"):
        export_organization(export_dir, "Nope")


def test_org_match_is_case_insensitive(export_dir):
    assert export_organization(export_dir, "finance").organization == "Finance"


def test_cli(export_dir, tmp_path, monkeypatch):
    from aap_migration.cli.main import cli

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    ok = runner.invoke(cli, ["cac-export", "--org", "Finance", "--export-dir", str(export_dir), "--out", "out"])
    assert ok.exit_code == 0, ok.output
    assert (tmp_path / "out" / "finance" / "controller_templates.yml").is_file()
    bad = runner.invoke(cli, ["cac-export", "--org", "Nope", "--export-dir", str(export_dir)])
    assert bad.exit_code == 2
    missing = runner.invoke(cli, ["cac-export", "--org", "Finance", "--export-dir", str(tmp_path / "none")])
    assert missing.exit_code == 2

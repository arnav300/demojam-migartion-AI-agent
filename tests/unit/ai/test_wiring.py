"""T14 wiring: CLI registration, interactive menu entries and .env.example."""

from pathlib import Path

import pytest

from aap_migration.cli import menu
from aap_migration.cli.main import cli

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def test_new_commands_registered():
    assert {"classify-failures", "remediate", "dashboard", "premigration-report", "enhanced-report"} <= set(
        cli.commands
    )


@pytest.mark.parametrize(
    ("name", "flag"),
    [("premigration-report", "--ai"), ("enhanced-report", "--ai"), ("classify-failures", "--ai"),
     ("remediate", "--ai"), ("dashboard", "--port")],
)
def test_command_options(name, flag):
    assert any(flag in p.opts for p in cli.commands[name].params)


def test_env_example_has_empty_ai_vars():
    lines = (ROOT / ".env.example").read_text().splitlines()
    for var in ("AI_ENDPOINT", "AI_MODEL", "AI_TOKEN"):
        assert f"{var}=" in lines, f"{var} must be present with an empty value"
    assert "AI_TIMEOUT_SECONDS=30" in lines


def _drive(monkeypatch, answers):
    answers = iter(answers)
    commands = []
    monkeypatch.setattr(menu.Prompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(menu, "run_command", lambda args, ctx=None: commands.append(args))
    monkeypatch.setattr(menu.Console, "clear", lambda self: None)
    return commands


def test_main_menu_opens_post_migration_submenu(monkeypatch):
    opened = []
    _drive(monkeypatch, ["6", "q"])
    monkeypatch.setattr(menu, "post_migration_submenu", lambda ctx: opened.append(ctx))
    menu.interactive_menu("ctx")
    assert opened == ["ctx"]


def test_post_migration_submenu_runs_new_commands(monkeypatch):
    monkeypatch.delenv("AI_ENDPOINT", raising=False)
    commands = _drive(monkeypatch, ["1", "", "2", "", "3", "", "4", "", "b"])
    menu.post_migration_submenu(None)
    assert commands == [
        ["enhanced-report"],
        ["classify-failures", "--output-dir", "reports"],
        ["remediate", "--from", "reports/classification.json", "--out", "remediation"],
        ["dashboard", "--port", "8080"],
    ]

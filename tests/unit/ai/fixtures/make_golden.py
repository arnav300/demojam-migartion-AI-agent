"""Regenerate premigration_golden.html (AI off). Run only when the HTML is meant to change:

    python tests/unit/ai/fixtures/make_golden.py
"""

import json
from pathlib import Path

from aap_migration.analysis.premigration import build_report_dict, run_analysis
from aap_migration.reporting.premigration_html import render_premigration_html

HERE = Path(__file__).parent
FIXED_GENERATED = "2026-10-19T12:00:00+00:00"


def build_report() -> dict:
    inventory = json.loads((HERE / "premigration_inventory.json").read_text())
    result = run_analysis(inventory, source_url="https://aap24.example.com", source_version="2.4")
    result.generated = FIXED_GENERATED
    return build_report_dict(result)


if __name__ == "__main__":
    (HERE / "premigration_golden.html").write_text(render_premigration_html(build_report()))

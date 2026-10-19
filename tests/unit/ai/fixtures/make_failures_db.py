"""Regenerate failures.db: one failure per RC code, one unknown, plus rows to ignore.

    python tests/unit/ai/fixtures/make_failures_db.py
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from aap_migration.migration.models import Base, MigrationProgress

HERE = Path(__file__).parent

# (resource_type, source_id, source_name, status, error_message) -> expected RC in the comment
ROWS = [
    ("job_templates", 101, "jt-playbook", "failed", "APIError: [400] Playbook not found for project 'p1'"),  # RC-001
    ("job_templates", 102, "jt-no-inv", "failed", "API error: inventory: This field may not be null."),  # RC-002
    ("schedules", 201, "sched-survey", "failed", "APIError: variables_needed_to_start: 'region' value missing"),  # RC-003
    ("schedules", 202, "sched-extra", "failed", "APIError: extra_data: Field is not allowed on launch"),  # RC-004
    ("workflow_job_templates", 301, "wf-broken", "failed", "Cannot import workflow: 2 template(s) failed to import"),  # RC-005
    ("inventory_sources", 401, "src-aws", "failed", "APIError: Credential is required for a cloud source."),  # RC-006
    ("credentials", 501, "cred-ssh", "failed", "APIError: ssh_key_unlock: should not be set when SSH key is not encrypted"),  # RC-007
    ("credentials", 502, "cred-dup", "failed", "Duplicate exists in target (source ID 502)"),  # RC-008
    ("instance_groups", 601, "ig-vm", "failed", "APIError: 'node1' is not a valid hostname in policy_instance_list"),  # RC-009
    ("job_templates", 103, "jt-validation", "failed", "Validation failed: Field 'foo' was removed. Rest."),  # RC-010
    ("users", 701, "ldap-user", "failed", "APIError: [400] Authenticator mapping missing for user"),  # RC-011
    ("projects", 801, "proj-weird", "failed", "Something unexpected happened."),  # UNCLASSIFIED
    ("projects", 802, "proj-ok", "completed", None),  # ignored: not failed
    ("projects", 803, "proj-empty", "failed", ""),  # ignored: no error message
]


def build(path: Path) -> None:
    path.unlink(missing_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for resource_type, source_id, name, status, error in ROWS:
            session.add(
                MigrationProgress(
                    resource_type=resource_type,
                    source_id=source_id,
                    source_name=name,
                    status=status,
                    phase="import",
                    error_message=error,
                )
            )
        session.commit()
    engine.dispose()


if __name__ == "__main__":
    build(HERE / "failures.db")

"""Deterministic mapping from post-migration error keys to RC codes (C8).

The keys are the labels produced by ``migration_report_v2.normalize_error_key``.
Rules decide the RC code; the model only explains it.  Lookup order:

1. exact key in :data:`ERROR_KEY_TO_RC`;
2. key prefix in :data:`ERROR_KEY_PREFIX_TO_RC` (for keys with dynamic parts);
3. resource type in :data:`RESOURCE_TYPE_TO_RC` (identity objects whose API
   errors have no dedicated key);
4. otherwise :data:`UNCLASSIFIED`.
"""

from __future__ import annotations

UNCLASSIFIED = "UNCLASSIFIED"

ERROR_KEY_TO_RC: dict[str, str] = {
    # RC-001 Missing or broken project / playbook
    "API: Playbook not found for project": "RC-001",
    "API: Job Template must have a project": "RC-001",
    # RC-002 Job template without inventory
    "API: Inventory required": "RC-002",
    "API: Cannot enable provisioning callback without inventory": "RC-002",
    # RC-003 Schedule missing required survey variables
    "API: variables_needed_to_start": "RC-003",
    # RC-004 Schedule overrides a field not allowed on launch
    "API: Field not configured to prompt on launch": "RC-004",
    "API: extra_data not allowed on launch": "RC-004",
    "API: Variables not allowed on launch": "RC-004",
    # RC-005 Broken workflow graph
    "Cannot import workflow: template(s) failed to import": "RC-005",
    "API: unified_job_template not found in target": "RC-005",
    # RC-006 Invalid inventory source configuration
    "API: Credential required for cloud source": "RC-006",
    "API: Cannot set source_path if not SCM type": "RC-006",
    # RC-007 Credential secrets
    "API: ssh_key_unlock set when key not encrypted": "RC-007",
    # RC-008 Duplicate identity collision
    "Duplicate exists in target": "RC-008",
    # RC-009 Platform topology mismatch (VM -> OpenShift)
    "API: Invalid hostname in policy_instance_list": "RC-009",
    "API: Host subscription limit reached": "RC-009",
    # RC-010 Feature deprecated or removed in AAP 2.6
    "API: Cannot create source for Smart/Constructed Inventory": "RC-010",
}

ERROR_KEY_PREFIX_TO_RC: tuple[tuple[str, str], ...] = (
    ("API: variables_needed_to_start (", "RC-003"),
    ("Duplicate ", "RC-008"),
    ("Validation: ", "RC-010"),
    ("API: Field required (", "RC-010"),
)

# RC-011 Identity & access model change (Platform Gateway)
RESOURCE_TYPE_TO_RC: dict[str, str] = {
    "users": "RC-011",
    "teams": "RC-011",
}


def rc_for_error(error_key: str, resource_type: str = "") -> str:
    """RC code for a normalised error key, or ``UNCLASSIFIED``."""
    if error_key in ERROR_KEY_TO_RC:
        return ERROR_KEY_TO_RC[error_key]
    for prefix, rc_code in ERROR_KEY_PREFIX_TO_RC:
        if error_key.startswith(prefix):
            return rc_code
    return RESOURCE_TYPE_TO_RC.get(resource_type, UNCLASSIFIED)

"""Pre-migration risk analysis for an AAP 2.4 estate.

Reads RAW AAP 2.4 objects (either from the ``export`` directory or live from
the source REST API) and predicts, *before anything is written to AAP 2.6*:

* which resources will fail to import and why (root causes RC-001 → RC-011),
* which resources will migrate but need a manual action (data loss, secrets,
  identity model changes),
* which resources are stale (abandoned / never used) and are cleanup
  candidates rather than migration candidates.

The rules are derived from the failure patterns that the post-migration
``enhanced-report`` classifies (see ``migration_report_v2.normalize_error_key``)
so that the same root-cause codes are used before and after migration.  The
JSON produced by :func:`build_report_dict` is the contract consumed by the
Dependency Analyzer UI and, later, by the OpenShift AI risk scorer.

The module is pure Python and has no side effects: it never writes to either
AAP instance.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Root-cause catalogue
# ---------------------------------------------------------------------------
RC_CATALOG: dict[str, dict[str, str]] = {
    "RC-001": {
        "title": "Missing or broken project / playbook",
        "description": (
            "The job template has no project or playbook, or its project cannot "
            "be synchronised. AAP 2.6 validates the playbook against a synced "
            "project at creation time, so the import is rejected."
        ),
        "remediation": (
            "Fix the project SCM URL / credential and run a successful sync on "
            "AAP 2.4, or delete the job template if it is no longer used."
        ),
        "post_migration_error": "API: Playbook not found for project / Job Template must have a project",
    },
    "RC-002": {
        "title": "Job template without inventory",
        "description": (
            "No inventory is assigned and 'Prompt on launch' for inventory is "
            "disabled (or a provisioning callback is enabled without an "
            "inventory). AAP 2.6 rejects these templates."
        ),
        "remediation": (
            "Assign an inventory or enable 'Prompt on launch' for inventory on "
            "the source template before migrating."
        ),
        "post_migration_error": "API: Inventory required / Cannot enable provisioning callback without inventory",
    },
    "RC-003": {
        "title": "Schedule missing required survey variables",
        "description": (
            "In AAP 2.4 a schedule could omit required survey variables and rely "
            "on survey defaults. AAP 2.6 requires every required survey variable "
            "to be present in the schedule's extra_data."
        ),
        "remediation": (
            "Add the listed variables (with their current default values) to the "
            "schedule's extra_data, or let the remediation playbook do it."
        ),
        "post_migration_error": "API: variables_needed_to_start",
    },
    "RC-004": {
        "title": "Schedule overrides a field not allowed on launch",
        "description": (
            "The schedule sets extra_data or a prompt (inventory, limit, tags, "
            "job type, ...) that the parent template no longer allows via "
            "'Prompt on launch'. Usually the template was edited after the "
            "schedule was created. AAP 2.6 validates this at creation time."
        ),
        "remediation": (
            "Enable the matching 'Prompt on launch' flag on the template or "
            "remove the override from the schedule."
        ),
        "post_migration_error": "API: Field not configured to prompt on launch / extra_data not allowed",
    },
    "RC-005": {
        "title": "Broken workflow graph",
        "description": (
            "One or more workflow nodes reference a template that was deleted "
            "(unified_job_template is null) or that is itself predicted to fail. "
            "The workflow cannot be imported until all node templates exist."
        ),
        "remediation": (
            "Remove or re-point the broken nodes on AAP 2.4, or fix the failing "
            "node templates first."
        ),
        "post_migration_error": "Cannot import workflow: template(s) failed to import",
    },
    "RC-006": {
        "title": "Invalid inventory source configuration",
        "description": (
            "The inventory source is inconsistent: a cloud source without "
            "credential, source_path on a non-SCM source, an SCM source without "
            "project, or a source attached to a Smart inventory."
        ),
        "remediation": (
            "Correct the source definition on AAP 2.4 (credential, source type, "
            "source project) or delete the orphaned source."
        ),
        "post_migration_error": "API: Credential required for cloud source / Cannot set source_path if not SCM type",
    },
    "RC-007": {
        "title": "Credential secrets (excluded — handled by migration tool)",
        "description": (
            "Credential secrets will be migrated using the dedicated secret "
            "migration tool that runs alongside the main migration. This root "
            "cause is excluded from the assessment."
        ),
        "remediation": (
            "No action needed: secrets are handled automatically by the "
            "migration toolkit."
        ),
        "post_migration_error": "(silent) credential imported without secret",
    },
    "RC-008": {
        "title": "Duplicate identity collision",
        "description": (
            "Two source objects resolve to the same identity on the target "
            "(same name, organization and type, or usernames that differ only by "
            "case). Only the first occurrence is imported, the others are skipped."
        ),
        "remediation": "Rename or merge the duplicates on AAP 2.4 before migrating.",
        "post_migration_error": "Duplicate exists in target / Skipped: Duplicate",
    },
    "RC-009": {
        "title": "Platform topology mismatch (VM → OpenShift)",
        "description": (
            "The object depends on the physical topology of the AAP 2.4 cluster "
            "(instance hostnames, VM instance groups, manual project paths on "
            "disk) or exceeds an organization host limit. These do not exist on "
            "AAP 2.6 running on OpenShift."
        ),
        "remediation": (
            "Recreate instance groups as container groups, move manual projects "
            "to SCM, and review organization max_hosts."
        ),
        "post_migration_error": "API: Invalid hostname in policy_instance_list / Host subscription limit reached",
    },
    "RC-010": {
        "title": "Feature deprecated or removed in AAP 2.6",
        "description": (
            "The object uses a feature that changed between AAP 2.4 and 2.6: "
            "custom virtualenvs, Ansible 2.9 execution environments, Smart "
            "inventories, or API fields that no longer exist on the target schema."
        ),
        "remediation": (
            "Move to a supported execution environment, convert Smart inventories "
            "to Constructed inventories, and review the removed fields."
        ),
        "post_migration_error": "Validation: field removed / field required",
    },
    "RC-011": {
        "title": "Identity & access model change (Platform Gateway)",
        "description": (
            "In AAP 2.6 users, teams and authentication move to the Platform "
            "Gateway. Users coming from LDAP / SAML / social auth need a Gateway "
            "authenticator, and legacy roles are translated, not copied."
        ),
        "remediation": (
            "Configure the matching Gateway authenticator before the IAM phase "
            "and review the role mapping table."
        ),
        "post_migration_error": "IAM: user/role mapping",
    },
    "STALE": {
        "title": "Stale data (cleanup candidate)",
        "description": (
            "The object looks abandoned: never run, failing for a long time, "
            "disabled, empty, or pending deletion. Migrating it adds noise and "
            "often causes failures."
        ),
        "remediation": "Confirm with the owner and delete it on AAP 2.4 instead of migrating it.",
        "post_migration_error": "(n/a)",
    },
}

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

# Human labels for the predicted outcome of a resource, by worst severity
OUTCOME_BY_SEVERITY = {
    "critical": "will_fail",
    "high": "at_risk",
    "medium": "needs_action",
    "low": "stale",
    "info": "ready",
    None: "ready",
}

OUTCOME_LABELS = {
    "will_fail": "Will fail",
    "at_risk": "At risk",
    "needs_action": "Needs action",
    "stale": "Stale",
    "ready": "Ready",
}

# Resource types analysed (export directory names == API endpoint names)
ANALYSED_TYPES = [
    "organizations",
    "users",
    "teams",
    "credential_types",
    "credentials",
    "execution_environments",
    "instance_groups",
    "projects",
    "inventories",
    "inventory_sources",
    "job_templates",
    "workflow_job_templates",
    "schedules",
    "notification_templates",
]

# unified_job_type (as reported in summary_fields) -> resource type
_UJT_TYPE_MAP = {
    "job": "job_templates",
    "workflow_job": "workflow_job_templates",
    "project_update": "projects",
    "inventory_update": "inventory_sources",
    "system_job": "system_job_templates",
}

# Inventory source types that do not need a credential
_NO_CRED_SOURCES = {"scm", "file", "constructed", ""}

# Schedule prompt fields -> template flag that must allow them
_SCHEDULE_PROMPTS = {
    "inventory": "ask_inventory_on_launch",
    "scm_branch": "ask_scm_branch_on_launch",
    "job_type": "ask_job_type_on_launch",
    "job_tags": "ask_tags_on_launch",
    "skip_tags": "ask_skip_tags_on_launch",
    "limit": "ask_limit_on_launch",
    "diff_mode": "ask_diff_mode_on_launch",
    "verbosity": "ask_verbosity_on_launch",
    "execution_environment": "ask_execution_environment_on_launch",
    "forks": "ask_forks_on_launch",
    "job_slice_count": "ask_job_slice_count_on_launch",
    "timeout": "ask_timeout_on_launch",
}

# Fields never considered when checking schema "removed" fields
_IGNORED_SCHEMA_FIELDS = {
    "id", "type", "url", "related", "summary_fields", "created", "modified",
    "_source_id", "nodes", "survey_spec", "name", "description",
}

_DEPRECATED_EE_MARKERS = ("ee-29", "ansible-2.9", "ee-2.9")

_STALE_TYPES = {
    "projects", "job_templates", "workflow_job_templates",
    "inventory_sources", "schedules", "inventories",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    """A single predicted problem for one source resource."""

    rc: str
    severity: str
    resource_type: str
    resource_id: int | None
    resource_name: str
    organization: str
    title: str
    detail: str
    remediation: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    cascade_from: str | None = None  # "type:id" of the resource that causes it

    @property
    def key(self) -> str:
        return f"{self.resource_type}:{self.resource_id}"


@dataclass
class PreMigrationResult:
    """Outcome of the analysis."""

    generated: str
    source_url: str
    source_version: str
    data_source: str
    inventory_counts: dict[str, int]
    findings: list[Finding]
    resources: dict[str, dict[str, Any]]  # key -> resource summary
    organizations: dict[str, dict[str, Any]]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_inventory_from_export(export_dir: Path) -> dict[str, list[dict]]:
    """Load RAW AAP 2.4 objects from the toolkit's export directory."""
    inventory: dict[str, list[dict]] = {}
    if not export_dir.is_dir():
        raise FileNotFoundError(f"Export directory not found: {export_dir}")

    for resource_dir in sorted(export_dir.iterdir()):
        if not resource_dir.is_dir():
            continue
        items: list[dict] = []
        for json_file in sorted(resource_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("premigration_export_file_unreadable", file=str(json_file), error=str(exc))
                continue
            if isinstance(data, list):
                items.extend(i for i in data if isinstance(i, dict))
        inventory[resource_dir.name] = items
    return inventory


async def fetch_inventory_live(
    client: Any,
    resource_types: list[str] | None = None,
    progress: Any = None,
) -> dict[str, list[dict]]:
    """Fetch the objects needed for the analysis directly from AAP 2.4.

    Read-only: only GET requests are issued. Workflow nodes and survey specs
    are fetched for the templates that need them, mirroring what ``export``
    embeds in its RAW files.
    """
    inventory: dict[str, list[dict]] = {}
    for rtype in resource_types or ANALYSED_TYPES:
        if progress:
            progress(f"Fetching {rtype} from source AAP...")
        try:
            inventory[rtype] = await client.get_paginated(f"{rtype}/")
        except Exception as exc:  # endpoint missing on this version, RBAC, ...
            logger.warning("premigration_live_fetch_failed", resource_type=rtype, error=str(exc))
            inventory[rtype] = []

    for jt in inventory.get("job_templates", []):
        if jt.get("survey_enabled"):
            try:
                spec = await client.get(f"job_templates/{jt['id']}/survey_spec/")
                if spec and spec.get("spec"):
                    jt["survey_spec"] = spec
            except Exception:
                pass

    if progress and inventory.get("workflow_job_templates"):
        progress("Fetching workflow nodes and surveys...")
    for wf in inventory.get("workflow_job_templates", []):
        try:
            wf["nodes"] = await client.get_workflow_nodes(wf["id"])
        except Exception:
            wf["nodes"] = []
        if wf.get("survey_enabled"):
            try:
                spec = await client.get(f"workflow_job_templates/{wf['id']}/survey_spec/")
                if spec and spec.get("spec"):
                    wf["survey_spec"] = spec
            except Exception:
                pass
    return inventory


def load_schema_comparison(path: Path | None) -> dict[str, Any]:
    """Load ``schemas/schema_comparison.json`` produced by ``aap-bridge prep``."""
    if not path or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("transformations", data) if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sf(item: dict) -> dict:
    sf = item.get("summary_fields")
    return sf if isinstance(sf, dict) else {}


def _rid(item: dict) -> int | None:
    rid = item.get("_source_id", item.get("id"))
    return rid if isinstance(rid, int) else None


def _name(rtype: str, item: dict) -> str:
    if rtype == "users":
        return item.get("username") or f"user_{_rid(item)}"
    return item.get("name") or item.get("hostname") or f"{rtype}_{_rid(item)}"


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_dict(value: Any) -> dict:
    """extra_data may come back as a dict or as a YAML/JSON string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            try:
                import yaml

                parsed = yaml.safe_load(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
    return {}


def _is_set(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


class _Index:
    """Lookup helpers across the whole inventory."""

    def __init__(self, inventory: dict[str, list[dict]]):
        self.inventory = inventory
        self.by_key: dict[tuple[str, int], dict] = {}
        for rtype, items in inventory.items():
            for item in items:
                rid = _rid(item)
                if rid is not None:
                    self.by_key[(rtype, rid)] = item
        self.org_names: dict[int, str] = {
            _rid(o): o.get("name", f"org_{_rid(o)}")
            for o in inventory.get("organizations", [])
            if _rid(o) is not None
        }
        self._org_cache: dict[tuple[str, int], str] = {}

    def get(self, rtype: str, rid: Any) -> dict | None:
        if isinstance(rid, dict):
            rid = rid.get("id")
        if not isinstance(rid, int):
            return None
        return self.by_key.get((rtype, rid))

    def org_of(self, rtype: str, item: dict) -> str:
        rid = _rid(item)
        cache_key = (rtype, rid) if rid is not None else None
        if cache_key and cache_key in self._org_cache:
            return self._org_cache[cache_key]
        org = self._resolve_org(rtype, item)
        if cache_key:
            self._org_cache[cache_key] = org
        return org

    def _resolve_org(self, rtype: str, item: dict) -> str:
        if rtype == "organizations":
            return item.get("name", "(Unknown)")
        sf = _sf(item)
        org = sf.get("organization")
        if isinstance(org, dict) and org.get("name"):
            return org["name"]
        org_id = item.get("organization")
        if isinstance(org_id, int) and org_id in self.org_names:
            return self.org_names[org_id]
        if rtype in ("inventory_sources", "hosts", "inventory_groups"):
            inv = self.get("inventories", item.get("inventory"))
            if inv:
                return self.org_of("inventories", inv)
        if rtype == "schedules":
            ujt = sf.get("unified_job_template") or {}
            parent_type = _UJT_TYPE_MAP.get(ujt.get("unified_job_type", ""))
            parent = self.get(parent_type, item.get("unified_job_template")) if parent_type else None
            if parent:
                return self.org_of(parent_type, parent)
        return "(Global)"


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------
class PreMigrationAnalyzer:
    """Apply the RC-001 → RC-011 and staleness rules to an AAP 2.4 inventory."""

    def __init__(
        self,
        inventory: dict[str, list[dict]],
        schema_comparison: dict[str, Any] | None = None,
        now: datetime | None = None,
    ):
        self.inventory = inventory
        self.schema = schema_comparison or {}
        self.idx = _Index(inventory)
        self.now = now or datetime.now(tz=UTC)
        self.findings: list[Finding] = []

    # -- public -------------------------------------------------------------
    def analyze(self) -> list[Finding]:
        self.findings = []
        self._check_projects()
        self._check_job_templates()
        self._check_workflows()
        self._check_schedules()
        self._check_inventory_sources()
        self._check_inventories()
        self._check_credentials()
        self._check_execution_environments()
        self._check_instance_groups()
        self._check_organizations()
        self._check_users()
        self._check_schema_drift()
        self._check_stale()
        self._propagate_cascades()
        self.findings.sort(
            key=lambda f: (-SEVERITY_ORDER[f.severity], f.rc, f.organization, f.resource_type, f.resource_name)
        )
        return self.findings

    # -- infrastructure -----------------------------------------------------
    def _add(
        self,
        rc: str,
        severity: str,
        rtype: str,
        item: dict,
        title: str,
        detail: str,
        remediation: str | None = None,
        evidence: dict[str, Any] | None = None,
        cascade_from: str | None = None,
    ) -> None:
        self.findings.append(
            Finding(
                rc=rc,
                severity=severity,
                resource_type=rtype,
                resource_id=_rid(item),
                resource_name=_name(rtype, item),
                organization=self.idx.org_of(rtype, item),
                title=title,
                detail=detail,
                remediation=remediation or RC_CATALOG[rc]["remediation"],
                evidence=evidence or {},
                cascade_from=cascade_from,
            )
        )

    def _items(self, rtype: str) -> list[dict]:
        return self.inventory.get(rtype, []) or []

    # -- RC-001 / RC-009: projects -----------------------------------------
    def _check_projects(self) -> None:
        for p in self._items("projects"):
            scm_type = p.get("scm_type") or ""
            status = (p.get("status") or "").lower()
            if scm_type and not p.get("scm_url"):
                self._add(
                    "RC-001", "critical", "projects", p,
                    "SCM project without URL",
                    f"Project is of type '{scm_type}' but has no scm_url; it can never sync on AAP 2.6.",
                    evidence={"scm_type": scm_type},
                )
            elif scm_type and (status in ("failed", "error") or p.get("last_update_failed")):
                self._add(
                    "RC-001", "high", "projects", p,
                    "Last project sync failed",
                    f"The last SCM update on AAP 2.4 ended with status '{status or 'failed'}'. "
                    "The same sync will run on AAP 2.6 and every job template using this "
                    "project will fail playbook validation if it does not succeed.",
                    evidence={"status": status, "scm_url": p.get("scm_url"), "last_updated": p.get("last_updated")},
                )
            elif scm_type and status == "never updated":
                self._add(
                    "RC-001", "high", "projects", p,
                    "Project never synced",
                    "The project has never been synchronised on AAP 2.4, so its playbooks "
                    "have never been validated.",
                    evidence={"status": status},
                )
            if not scm_type:
                self._add(
                    "RC-009", "high", "projects", p,
                    "Manual project (local path on disk)",
                    f"Manual project using local_path '{p.get('local_path', '')}'. AAP 2.6 on "
                    "OpenShift has no shared /var/lib/awx/projects from the 2.4 VMs: the "
                    "playbooks will not be found unless the content is moved to SCM or a "
                    "persistent projects volume is provisioned.",
                    remediation="Move the playbooks to a Git repository and convert the project to SCM.",
                    evidence={"local_path": p.get("local_path")},
                )
            if p.get("custom_virtualenv"):
                self._add(
                    "RC-010", "medium", "projects", p,
                    "Custom virtualenv configured",
                    f"custom_virtualenv '{p['custom_virtualenv']}' is not supported in AAP 2.6; "
                    "an execution environment must be used instead.",
                    evidence={"custom_virtualenv": p["custom_virtualenv"]},
                )

    # -- RC-001 / RC-002 / RC-010: job templates ---------------------------
    def _check_job_templates(self) -> None:
        for jt in self._items("job_templates"):
            sf = _sf(jt)
            if not jt.get("project") and not sf.get("project"):
                self._add(
                    "RC-001", "critical", "job_templates", jt,
                    "Job template without project",
                    "No project is assigned (it was probably deleted). AAP 2.6 rejects job "
                    "templates without a project.",
                    remediation="Assign an existing project on AAP 2.4 or delete the job template.",
                )
            elif not jt.get("playbook"):
                self._add(
                    "RC-001", "critical", "job_templates", jt,
                    "Job template without playbook",
                    "The playbook field is empty, so playbook validation on AAP 2.6 fails.",
                    remediation="Select a playbook from the project on AAP 2.4 or delete the job template.",
                )

            has_inventory = bool(jt.get("inventory") or sf.get("inventory"))
            if not has_inventory and not jt.get("ask_inventory_on_launch"):
                self._add(
                    "RC-002", "critical", "job_templates", jt,
                    "No inventory and inventory not prompted",
                    "The template has no inventory and 'Prompt on launch' for inventory is "
                    "disabled.",
                    evidence={"ask_inventory_on_launch": jt.get("ask_inventory_on_launch")},
                )
            elif not has_inventory and jt.get("host_config_key"):
                self._add(
                    "RC-002", "critical", "job_templates", jt,
                    "Provisioning callback without inventory",
                    "A provisioning callback (host_config_key) is enabled but no inventory "
                    "is assigned; AAP 2.6 refuses this combination.",
                )

            if jt.get("custom_virtualenv"):
                self._add(
                    "RC-010", "medium", "job_templates", jt,
                    "Custom virtualenv configured",
                    f"custom_virtualenv '{jt['custom_virtualenv']}' is ignored on AAP 2.6; "
                    "the template will run in the default execution environment.",
                    evidence={"custom_virtualenv": jt["custom_virtualenv"]},
                )

            ee = self.idx.get("execution_environments", jt.get("execution_environment"))
            if ee and self._is_deprecated_ee(ee):
                self._add(
                    "RC-010", "medium", "job_templates", jt,
                    "Uses a deprecated Ansible 2.9 execution environment",
                    f"Execution environment '{ee.get('name')}' ({ee.get('image')}) is based on "
                    "Ansible 2.9, which is not shipped with AAP 2.6.",
                    evidence={"execution_environment": ee.get("name"), "image": ee.get("image")},
                )

    # -- RC-005: workflows --------------------------------------------------
    def _check_workflows(self) -> None:
        for wf in self._items("workflow_job_templates"):
            nodes = wf.get("nodes")
            if not isinstance(nodes, list):
                continue
            broken = [n for n in nodes if isinstance(n, dict) and n.get("unified_job_template") is None]
            if broken:
                self._add(
                    "RC-005", "critical", "workflow_job_templates", wf,
                    f"{len(broken)} workflow node(s) point to a deleted template",
                    f"{len(broken)} of {len(nodes)} nodes have unified_job_template = null. "
                    "The workflow cannot be rebuilt on AAP 2.6 until these nodes are removed "
                    "or re-pointed.",
                    evidence={"broken_node_ids": [n.get("id") for n in broken][:20], "total_nodes": len(nodes)},
                )

    # -- RC-003 / RC-004: schedules ------------------------------------------
    def _check_schedules(self) -> None:
        for s in self._items("schedules"):
            sf = _sf(s)
            ujt = sf.get("unified_job_template") or {}
            parent_type = _UJT_TYPE_MAP.get(ujt.get("unified_job_type", ""))
            parent = self.idx.get(parent_type, s.get("unified_job_template")) if parent_type else None

            if s.get("unified_job_template") is None:
                self._add(
                    "RC-004", "critical", "schedules", s,
                    "Orphan schedule",
                    "The schedule has no parent template (unified_job_template is null).",
                    remediation="Delete the orphan schedule on AAP 2.4.",
                )
                continue
            if parent is None or parent_type not in ("job_templates", "workflow_job_templates"):
                continue  # project / inventory updates have no prompts

            extra = _as_dict(s.get("extra_data"))
            survey_vars, required_vars = self._survey_vars(parent)

            # RC-003: required survey variables missing from extra_data
            if parent.get("survey_enabled") and required_vars:
                missing = sorted(v for v in required_vars if v not in extra)
                if missing:
                    self._add(
                        "RC-003", "critical", "schedules", s,
                        "Required survey variables missing from schedule",
                        f"Parent template '{_name(parent_type, parent)}' has a survey with required "
                        f"variable(s) {', '.join(missing)} not present in the schedule's extra_data. "
                        "AAP 2.4 used the survey defaults at runtime; AAP 2.6 rejects the schedule.",
                        evidence={
                            "missing_variables": missing,
                            "defaults": {v: required_vars[v] for v in missing},
                            "parent": _name(parent_type, parent),
                        },
                    )

            # RC-004: extra_data not allowed
            if extra:
                allows_vars = bool(parent.get("ask_variables_on_launch"))
                if not allows_vars:
                    if not parent.get("survey_enabled"):
                        self._add(
                            "RC-004", "critical", "schedules", s,
                            "extra_data not allowed on launch",
                            f"The schedule passes extra_data ({', '.join(sorted(extra)[:5])}) but "
                            f"'{_name(parent_type, parent)}' has ask_variables_on_launch=false and no survey.",
                            evidence={"extra_data_keys": sorted(extra)},
                        )
                    else:
                        not_in_survey = sorted(k for k in extra if k not in survey_vars)
                        if not_in_survey:
                            self._add(
                                "RC-004", "critical", "schedules", s,
                                "Variables not defined in survey",
                                f"extra_data keys {', '.join(not_in_survey[:5])} are not survey "
                                f"variables of '{_name(parent_type, parent)}' and the template does "
                                "not prompt for variables.",
                                evidence={"keys": not_in_survey},
                            )

            # RC-004: prompt overrides not allowed by parent
            disallowed = []
            for fld, flag in _SCHEDULE_PROMPTS.items():
                if _is_set(s.get(fld)) and flag in parent and not parent.get(flag):
                    disallowed.append(fld)
            if disallowed:
                self._add(
                    "RC-004", "critical", "schedules", s,
                    "Overrides fields not prompted on launch",
                    f"The schedule overrides {', '.join(disallowed)} but "
                    f"'{_name(parent_type, parent)}' no longer prompts for "
                    f"{'it' if len(disallowed) == 1 else 'them'}.",
                    evidence={"fields": disallowed},
                )

    def _survey_vars(self, template: dict) -> tuple[set[str], dict[str, Any]]:
        spec = template.get("survey_spec") or {}
        questions = spec.get("spec") if isinstance(spec, dict) else None
        all_vars: set[str] = set()
        required: dict[str, Any] = {}
        for q in questions or []:
            if not isinstance(q, dict) or not q.get("variable"):
                continue
            all_vars.add(q["variable"])
            if q.get("required"):
                required[q["variable"]] = q.get("default")
        return all_vars, required

    # -- RC-006: inventory sources -------------------------------------------
    def _check_inventory_sources(self) -> None:
        for src in self._items("inventory_sources"):
            source = src.get("source") or ""
            inv = self.idx.get("inventories", src.get("inventory"))
            inv_kind = (inv or {}).get("kind") or ""

            if inv_kind == "smart":
                self._add(
                    "RC-006", "critical", "inventory_sources", src,
                    "Source attached to a Smart inventory",
                    "AAP 2.6 does not allow inventory sources on Smart inventories.",
                    evidence={"inventory": (inv or {}).get("name")},
                )
                continue
            if source == "constructed" or inv_kind == "constructed":
                continue  # managed automatically by the target
            if source == "scm" and not src.get("source_project"):
                self._add(
                    "RC-006", "critical", "inventory_sources", src,
                    "SCM source without project",
                    "Sourced-from-project inventory source has no source_project.",
                )
            if src.get("source_path") and source != "scm":
                self._add(
                    "RC-006", "critical", "inventory_sources", src,
                    "source_path set on non-SCM source",
                    f"source_path '{src.get('source_path')}' is only valid for SCM sources (source='{source}').",
                )
            if source not in _NO_CRED_SOURCES and not src.get("credential") and not _sf(src).get("credentials"):
                self._add(
                    "RC-006", "critical", "inventory_sources", src,
                    "Cloud source without credential",
                    f"Inventory source of type '{source}' has no credential assigned.",
                    evidence={"source": source},
                )
            if src.get("custom_virtualenv"):
                self._add(
                    "RC-010", "medium", "inventory_sources", src,
                    "Custom virtualenv configured",
                    f"custom_virtualenv '{src['custom_virtualenv']}' is not supported in AAP 2.6.",
                )

    # -- inventories ----------------------------------------------------------
    def _check_inventories(self) -> None:
        for inv in self._items("inventories"):
            if inv.get("kind") == "smart":
                self._add(
                    "RC-010", "low", "inventories", inv,
                    "Smart inventory (deprecated)",
                    "Smart inventories are deprecated in favour of Constructed inventories. "
                    "They migrate, but host_filter semantics should be re-validated on 2.6.",
                    remediation="Plan a conversion to a Constructed inventory.",
                    evidence={"host_filter": inv.get("host_filter")},
                )

    # -- RC-007 / RC-008: credentials -----------------------------------------
    # RC-007 (credential secrets) is excluded from the assessment because
    # secrets are migrated by the dedicated secret-migration tool that runs
    # alongside the main migration.  Only RC-008 (duplicate identity) is
    # checked here.
    def _check_credentials(self) -> None:
        seen: dict[tuple, dict] = {}
        for cred in self._items("credentials"):
            if cred.get("managed"):
                continue

            key = (cred.get("name"), cred.get("organization"), cred.get("credential_type"))
            if key in seen:
                first = seen[key]
                self._add(
                    "RC-008", "high", "credentials", cred,
                    "Duplicate credential (name + org + type)",
                    f"Same name, organization and type as credential ID {_rid(first)}; only the "
                    "first one is imported, templates pointing to this one lose their credential.",
                    evidence={"duplicate_of": _rid(first)},
                )
            else:
                seen[key] = cred

    # -- RC-010: execution environments ---------------------------------------
    def _is_deprecated_ee(self, ee: dict) -> bool:
        image = (ee.get("image") or "").lower()
        return any(marker in image for marker in _DEPRECATED_EE_MARKERS)

    def _check_execution_environments(self) -> None:
        for ee in self._items("execution_environments"):
            if self._is_deprecated_ee(ee):
                self._add(
                    "RC-010", "medium", "execution_environments", ee,
                    "Ansible 2.9 execution environment",
                    f"Image '{ee.get('image')}' is based on Ansible 2.9 and is not provided by AAP 2.6.",
                    remediation="Rebuild the content on a supported ee-supported / ee-minimal image.",
                    evidence={"image": ee.get("image")},
                )

    # -- RC-009: instance groups ----------------------------------------------
    def _check_instance_groups(self) -> None:
        for ig in self._items("instance_groups"):
            if ig.get("is_container_group"):
                continue
            name = ig.get("name", "")
            if ig.get("policy_instance_list"):
                self._add(
                    "RC-009", "high", "instance_groups", ig,
                    "Instance group pinned to VM hostnames",
                    f"policy_instance_list {ig['policy_instance_list']} references AAP 2.4 node "
                    "hostnames that do not exist on the OpenShift-based AAP 2.6.",
                    evidence={"policy_instance_list": ig["policy_instance_list"]},
                )
            elif name not in ("controlplane", "default"):
                self._add(
                    "RC-009", "medium", "instance_groups", ig,
                    "VM-based instance group",
                    "On AAP 2.6 on OpenShift, custom capacity is provided by container groups; "
                    "this group will exist but have no instances.",
                    remediation="Recreate it as a container group with an appropriate pod spec.",
                )

    # -- RC-009 / RC-010: organizations ---------------------------------------
    def _check_organizations(self) -> None:
        hosts_per_org: Counter = Counter()
        for inv in self._items("inventories"):
            if inv.get("kind") in ("smart", "constructed"):
                continue  # these reference hosts of other inventories
            hosts_per_org[self.idx.org_of("inventories", inv)] += int(inv.get("total_hosts") or 0)
        for org in self._items("organizations"):
            max_hosts = org.get("max_hosts") or 0
            used = hosts_per_org.get(org.get("name"), 0)
            if max_hosts and used > max_hosts:
                self._add(
                    "RC-009", "high", "organizations", org,
                    "Organization exceeds max_hosts",
                    f"The organization owns {used} hosts but max_hosts is {max_hosts}; host "
                    "import will stop at the limit.",
                    evidence={"max_hosts": max_hosts, "hosts": used},
                )
            if org.get("custom_virtualenv"):
                self._add(
                    "RC-010", "medium", "organizations", org,
                    "Custom virtualenv configured",
                    f"custom_virtualenv '{org['custom_virtualenv']}' is not supported in AAP 2.6.",
                )

    # -- RC-008 / RC-011: users ----------------------------------------------
    def _check_users(self) -> None:
        by_lower: dict[str, dict] = {}
        for u in self._items("users"):
            username = (u.get("username") or "").strip()
            external = u.get("ldap_dn") or u.get("external_account") or u.get("auth")
            if external:
                source = "LDAP" if u.get("ldap_dn") else (u.get("external_account") or "social/SAML")
                self._add(
                    "RC-011", "medium", "users", u,
                    f"Externally authenticated user ({source})",
                    "The user logs in through an external identity provider configured on the "
                    "Controller. In AAP 2.6 authentication is handled by the Platform Gateway: "
                    "the matching authenticator must exist before users can log in.",
                    evidence={"source": source},
                )
            low = username.lower()
            if low and low in by_lower:
                self._add(
                    "RC-008", "medium", "users", u,
                    "Username differs only by case",
                    f"'{username}' collides with '{by_lower[low].get('username')}' in a "
                    "case-insensitive identity provider.",
                    evidence={"collides_with": by_lower[low].get("username")},
                )
            elif low:
                by_lower[low] = u

    # -- RC-010: schema drift (requires `aap-bridge prep`) -------------------
    def _check_schema_drift(self) -> None:
        if not self.schema:
            return
        for rtype, rules in self.schema.items():
            if not isinstance(rules, dict):
                continue
            removed = [f for f in rules.get("fields_removed", []) if f not in _IGNORED_SCHEMA_FIELDS]
            now_required = [
                f for f, ch in (rules.get("fields_required_changed") or {}).items()
                if isinstance(ch, dict) and ch.get("target_required") and not ch.get("source_required")
            ]
            if not removed and not now_required:
                continue
            for item in self._items(rtype):
                lost = sorted(f for f in removed if _is_set(item.get(f)))
                if lost:
                    self._add(
                        "RC-010", "medium", rtype, item,
                        "Uses fields removed in AAP 2.6",
                        f"Values in {', '.join(lost)} have no equivalent in the AAP 2.6 API and "
                        "will be dropped by the transform phase.",
                        evidence={"fields": lost, "values": {f: item.get(f) for f in lost}},
                    )
                missing = sorted(f for f in now_required if not _is_set(item.get(f)))
                if missing:
                    self._add(
                        "RC-010", "high", rtype, item,
                        "Field required by AAP 2.6 is empty",
                        f"{', '.join(missing)} became mandatory on AAP 2.6 and is empty in the source.",
                        evidence={"fields": missing},
                    )

    # -- STALE -------------------------------------------------------------
    def _check_stale(self) -> None:
        from aap_migration.cli.commands.migration_report_v2 import (
            _determine_resource_status,
            extract_export_metadata,
        )

        for rtype in _STALE_TYPES:
            for item in self._items(rtype):
                meta = extract_export_metadata(rtype, item)
                meta["resource_type"] = rtype
                status = _determine_resource_status(meta)
                if status != "Probably Stale":
                    continue
                reasons = self._stale_reasons(rtype, meta)
                self._add(
                    "STALE", "low", rtype, item,
                    reasons[0] if reasons else "Matches staleness heuristics",
                    " ".join(reasons[1:]) if len(reasons) > 1 else (
                        "Classified 'Probably Stale' by the same heuristics used by enhanced-report."
                    ),
                    evidence={
                        k: meta.get(k) for k in (
                            "modified", "modified_by", "last_job_run", "last_job_failed",
                            "sync_status", "next_run", "total_hosts",
                        ) if meta.get(k) not in (None, "")
                    },
                )

    def _stale_reasons(self, rtype: str, m: dict) -> list[str]:
        """Human-readable explanation mirroring ``_determine_resource_status``."""
        one_year = self.now - timedelta(days=365)
        six_months = self.now - timedelta(days=180)
        modified = _parse_dt(m.get("modified"))
        last_run = _parse_dt(m.get("last_job_run"))
        reasons: list[str] = []

        def age(dt: datetime | None) -> str:
            return f"{(self.now - dt).days} days ago" if dt else "never"

        if rtype == "projects":
            reasons.append("Old project with failing sync")
            reasons.append(f"Last activity {age(last_run or modified)}, last sync status '{m.get('sync_status')}'.")
        elif rtype == "workflow_job_templates":
            if m.get("wf_total_nodes") == 0:
                reasons.append("Empty workflow (no nodes)")
            elif m.get("wf_null_node_count"):
                reasons.append("Workflow with deleted nodes")
            elif last_run and last_run < one_year:
                reasons.append(f"Workflow not run for over a year (last run {age(last_run)})")
            elif last_run and m.get("last_job_failed") and last_run < six_months:
                reasons.append("Workflow failing for more than 6 months")
            else:
                reasons.append(f"Workflow never run, last modified {age(modified)}")
        elif rtype == "job_templates":
            if not m.get("last_job_run") and not m.get("playbook"):
                reasons.append("Never run and no playbook")
            elif last_run and last_run < one_year and m.get("last_job_failed"):
                reasons.append(f"Last run failed {age(last_run)}")
            elif not m.get("inventory_name") and not m.get("ask_inventory_on_launch"):
                reasons.append("No inventory and inventory not prompted")
            elif not m.get("project_name"):
                reasons.append("Project deleted (no project linked)")
            else:
                reasons.append("Matches staleness heuristics")
        elif rtype == "inventory_sources":
            if not m.get("credential_names") and (m.get("source") or "") not in _NO_CRED_SOURCES:
                reasons.append("Cloud source without credential")
            elif m.get("sync_status") == "never updated":
                reasons.append("Inventory source never synced")
            else:
                reasons.append("Sync failing for over a year")
        elif rtype == "schedules":
            reasons.append("Disabled schedule")
            reasons.append(f"Disabled, last modified {age(modified)}, next run: {m.get('next_run') or 'none'}.")
        elif rtype == "inventories":
            if m.get("pending_deletion"):
                reasons.append("Inventory pending deletion")
            elif m.get("total_hosts") == 0:
                reasons.append(f"Empty inventory, last modified {age(modified)}")
            else:
                reasons.append("All inventory sources failing")
        if last_run is None and rtype in ("job_templates", "workflow_job_templates") and modified:
            reasons.append(f"Never executed; last modified {age(modified)} by {m.get('modified_by', 'N/A')}.")
        return reasons

    # -- cascades -------------------------------------------------------------
    def _propagate_cascades(self) -> None:
        """Resources that depend on a failing resource will fail as well."""
        worst: dict[str, str] = {}
        for f in self.findings:
            if f.rc == "STALE":
                continue
            cur = worst.get(f.key)
            if cur is None or SEVERITY_ORDER[f.severity] > SEVERITY_ORDER[cur]:
                worst[f.key] = f.severity

        def failing(key: str, min_sev: str = "high") -> bool:
            return key in worst and SEVERITY_ORDER[worst[key]] >= SEVERITY_ORDER[min_sev]

        # Projects -> job templates
        for jt in self._items("job_templates"):
            pkey = f"projects:{jt.get('project')}"
            if jt.get("project") and failing(pkey) and not failing(f"job_templates:{_rid(jt)}", "critical"):
                proj = self.idx.get("projects", jt.get("project")) or {}
                self._add(
                    "RC-001", "high", "job_templates", jt,
                    "Depends on a failing project",
                    f"Project '{proj.get('name', jt.get('project'))}' is predicted to fail sync; the "
                    f"playbook '{jt.get('playbook')}' will not be found on AAP 2.6.",
                    cascade_from=pkey,
                )
                worst.setdefault(f"job_templates:{_rid(jt)}", "high")

        # Node templates -> workflows
        for wf in self._items("workflow_job_templates"):
            wkey = f"workflow_job_templates:{_rid(wf)}"
            if failing(wkey, "critical"):
                continue
            bad = []
            for n in wf.get("nodes") or []:
                ujt = n.get("unified_job_template")
                ntype = _UJT_TYPE_MAP.get((_sf(n).get("unified_job_template") or {}).get("unified_job_type", ""), "job_templates")
                if ujt is not None and failing(f"{ntype}:{ujt}", "critical"):
                    bad.append(f"{ntype}:{ujt}")
            if bad:
                self._add(
                    "RC-005", "high", "workflow_job_templates", wf,
                    f"{len(bad)} node template(s) predicted to fail",
                    "The workflow references templates that will not be imported, so its nodes "
                    "cannot be created.",
                    evidence={"failing_nodes": bad[:20]},
                    cascade_from=bad[0],
                )

        # Parent templates -> schedules
        for s in self._items("schedules"):
            ujt = _sf(s).get("unified_job_template") or {}
            ptype = _UJT_TYPE_MAP.get(ujt.get("unified_job_type", ""))
            pkey = f"{ptype}:{s.get('unified_job_template')}"
            if ptype and failing(pkey, "critical") and not failing(f"schedules:{_rid(s)}", "critical"):
                self._add(
                    "RC-004", "high", "schedules", s,
                    "Parent template predicted to fail",
                    f"'{ujt.get('name', pkey)}' will not be imported, so this schedule will fail "
                    "with 'unified_job_template not found in target'.",
                    cascade_from=pkey,
                )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def summarize(
    inventory: dict[str, list[dict]],
    findings: list[Finding],
    idx: _Index | None = None,
    include_healthy: bool = False,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Build per-resource and per-organization summaries."""
    idx = idx or _Index(inventory)
    from aap_migration.cli.commands.migration_report_v2 import extract_export_metadata

    by_key: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_key[f.key].append(f)

    resources: dict[str, dict] = {}
    orgs: dict[str, dict] = defaultdict(lambda: {
        "total": 0,
        "by_type": Counter(),
        "outcomes": Counter(),
        "severity": Counter(),
        "rc": Counter(),
    })

    for rtype in ANALYSED_TYPES:
        for item in inventory.get(rtype, []) or []:
            rid = _rid(item)
            key = f"{rtype}:{rid}"
            org = idx.org_of(rtype, item)
            flist = by_key.get(key, [])
            real = [f for f in flist if f.rc != "STALE"]
            worst = max((f.severity for f in real), key=lambda s: SEVERITY_ORDER[s], default=None)
            if worst in ("low", "info"):
                worst = None
            outcome = OUTCOME_BY_SEVERITY[worst]
            stale = any(f.rc == "STALE" for f in flist)
            if outcome == "ready" and stale:
                outcome = "stale"

            o = orgs[org]
            o["total"] += 1
            o["by_type"][rtype] += 1
            o["outcomes"][outcome] += 1
            for f in flist:
                o["severity"][f.severity] += 1
                o["rc"][f.rc] += 1

            if not flist and not include_healthy:
                continue
            meta = extract_export_metadata(rtype, item)
            resources[key] = {
                "key": key,
                "resource_type": rtype,
                "id": rid,
                "name": _name(rtype, item),
                "organization": org,
                "outcome": outcome,
                "stale": stale,
                "rcs": sorted({f.rc for f in flist}),
                "modified": meta.get("modified", ""),
                "modified_by": meta.get("modified_by", "N/A"),
                "created_by": meta.get("created_by", "N/A"),
                "last_job_run": meta.get("last_job_run", ""),
                "last_job_failed": meta.get("last_job_failed"),
                "sync_status": meta.get("sync_status", ""),
            }

    org_out: dict[str, dict] = {}
    for name, o in orgs.items():
        total = o["total"] or 1
        fail = o["outcomes"].get("will_fail", 0)
        risk = o["outcomes"].get("at_risk", 0)
        org_out[name] = {
            "total": o["total"],
            "by_type": dict(o["by_type"]),
            "outcomes": dict(o["outcomes"]),
            "severity": dict(o["severity"]),
            "rc": dict(o["rc"]),
            "predicted_success_pct": round(100 * (o["total"] - fail) / total, 1),
            "readiness_pct": round(100 * (o["total"] - fail - risk) / total, 1),
        }
    return resources, org_out


def run_analysis(
    inventory: dict[str, list[dict]],
    source_url: str = "",
    source_version: str = "",
    data_source: str = "export",
    schema_comparison: dict[str, Any] | None = None,
    include_healthy: bool = False,
) -> PreMigrationResult:
    analyzer = PreMigrationAnalyzer(inventory, schema_comparison)
    findings = analyzer.analyze()
    resources, orgs = summarize(inventory, findings, analyzer.idx, include_healthy)
    return PreMigrationResult(
        generated=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        source_url=source_url,
        source_version=source_version,
        data_source=data_source,
        inventory_counts={rt: len(inventory.get(rt, []) or []) for rt in ANALYSED_TYPES if inventory.get(rt)},
        findings=findings,
        resources=resources,
        organizations=orgs,
    )


def build_report_dict(result: PreMigrationResult) -> dict[str, Any]:
    """Serialisable contract shared with the Dependency Analyzer and RHOAI."""
    total = sum(o["total"] for o in result.organizations.values())
    outcomes: Counter = Counter()
    for o in result.organizations.values():
        outcomes.update(o["outcomes"])
    sev = Counter(f.severity for f in result.findings)
    rc = Counter(f.rc for f in result.findings)
    rc_resources: dict[str, set] = defaultdict(set)
    for f in result.findings:
        rc_resources[f.rc].add(f.key)
    by_type_out: dict[str, Counter] = defaultdict(Counter)
    for r in result.resources.values():
        by_type_out[r["resource_type"]][r["outcome"]] += 1

    will_fail = outcomes.get("will_fail", 0)
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "premigration",
        "generated": result.generated,
        "source": {
            "url": result.source_url,
            "version": result.source_version,
            "data_source": result.data_source,
        },
        "target": {"version": "2.6", "platform": "OpenShift"},
        "summary": {
            "total_resources": total,
            "inventory_counts": result.inventory_counts,
            "outcomes": {k: outcomes.get(k, 0) for k in OUTCOME_LABELS},
            "findings_total": len(result.findings),
            "findings_by_severity": {k: sev.get(k, 0) for k in SEVERITY_ORDER},
            "findings_by_rc": dict(sorted(rc.items())),
            "resources_by_rc": {k: len(v) for k, v in sorted(rc_resources.items())},
            "outcomes_by_type": {k: dict(v) for k, v in sorted(by_type_out.items())},
            "predicted_success_pct": round(100 * (total - will_fail) / total, 1) if total else 100.0,
            "organizations": len(result.organizations),
        },
        "rc_catalog": RC_CATALOG,
        "outcome_labels": OUTCOME_LABELS,
        "organizations": dict(sorted(result.organizations.items())),
        "resources": result.resources,
        "findings": [
            {**asdict(f), "key": f.key} for f in result.findings
        ],
    }

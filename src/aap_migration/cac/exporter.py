"""Convert the staged AAP 2.4 export of ONE organization into var files for the
``infra.aap_configuration`` collection (``dispatch`` role).

* Variable names follow the collection (checked against release 4.10.0):
  ``aap_organizations``, ``aap_teams``, ``controller_credential_types``,
  ``controller_credentials``, ``controller_projects``, ``controller_inventories``,
  ``controller_inventory_sources``, ``controller_hosts``, ``controller_groups``,
  ``controller_templates``, ``controller_workflows``, ``controller_schedules``.
* References use names, never numeric IDs.
* Every secret becomes a ``{{ vault_<object>_<field> }}`` placeholder: credential
  inputs the credential type marks as secret, and any ``$encrypted$`` value.
  Real values never reach the output, even when the export holds them.
* Other strings containing Jinja (``{{`` / ``{%``) are tagged ``!unsafe`` so
  Ansible does not template them when the var files are loaded.

Pure function of the export directory: nothing is read from or written to AAP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from aap_migration.analysis.premigration import load_inventory_from_export

ENCRYPTED = "$encrypted$"

# Output order = apply order used by the dispatch role.
VAR_NAMES = (
    "aap_organizations",
    "aap_teams",
    "controller_credential_types",
    "controller_credentials",
    "controller_projects",
    "controller_inventories",
    "controller_inventory_sources",
    "controller_hosts",
    "controller_groups",
    "controller_templates",
    "controller_workflows",
    "controller_schedules",
)

_UJT_TYPES = {
    "job": "job_template",
    "job_template": "job_template",
    "workflow_job": "workflow_job_template",
    "workflow_job_template": "workflow_job_template",
    "project_update": "project",
    "project": "project",
    "inventory_update": "inventory_source",
    "inventory_source": "inventory_source",
}

_TEMPLATE_FIELDS = (
    "description", "job_type", "playbook", "scm_branch", "forks", "limit", "verbosity",
    "job_tags", "skip_tags", "timeout", "use_fact_cache", "host_config_key", "become_enabled",
    "diff_mode", "allow_simultaneous", "job_slice_count", "webhook_service",
    "prevent_instance_group_fallback", "survey_enabled",
    "ask_scm_branch_on_launch", "ask_diff_mode_on_launch", "ask_variables_on_launch",
    "ask_limit_on_launch", "ask_tags_on_launch", "ask_skip_tags_on_launch",
    "ask_job_type_on_launch", "ask_verbosity_on_launch", "ask_inventory_on_launch",
    "ask_credential_on_launch", "ask_execution_environment_on_launch", "ask_labels_on_launch",
    "ask_forks_on_launch", "ask_job_slice_count_on_launch", "ask_timeout_on_launch",
    "ask_instance_groups_on_launch",
)
_WORKFLOW_FIELDS = (
    "description", "scm_branch", "limit", "allow_simultaneous", "survey_enabled",
    "webhook_service", "ask_variables_on_launch", "ask_inventory_on_launch",
    "ask_scm_branch_on_launch", "ask_limit_on_launch", "ask_labels_on_launch",
    "ask_tags_on_launch", "ask_skip_tags_on_launch", "job_tags", "skip_tags",
)
_PROJECT_FIELDS = (
    "description", "scm_type", "scm_url", "scm_branch", "scm_refspec", "scm_clean",
    "scm_delete_on_update", "scm_track_submodules", "scm_update_on_launch",
    "scm_update_cache_timeout", "allow_override", "timeout",
)
_SOURCE_FIELDS = (
    "description", "source", "source_path", "overwrite", "overwrite_vars", "update_on_launch",
    "update_cache_timeout", "timeout", "verbosity", "enabled_var", "enabled_value",
    "host_filter", "limit", "scm_branch",
)
_SCHEDULE_FIELDS = (
    "description", "rrule", "enabled", "limit", "job_type", "job_tags", "skip_tags",
    "scm_branch", "verbosity", "diff_mode", "forks", "timeout", "job_slice_count",
)


class UnsafeText(str):
    """A string Ansible must not template (dumped with the ``!unsafe`` tag)."""


class VaultRef(str):
    """A ``{{ vault_* }}`` placeholder: must stay templatable, never ``!unsafe``."""


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(
    UnsafeText, lambda dumper, data: dumper.represent_scalar("!unsafe", str(data))
)
_Dumper.add_representer(VaultRef, lambda dumper, data: dumper.represent_str(str(data)))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "x"


@dataclass
class CaCResult:
    organization: str
    variables: dict[str, list[dict[str, Any]]]
    vault_vars: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.variables.items()}


class _Export:
    """The export plus id -> object lookups."""

    def __init__(self, inventory: dict[str, list[dict]]) -> None:
        self.data = inventory
        groups = inventory.get("inventory_groups") or inventory.get("groups") or []
        self.data["inventory_groups"] = groups
        self.by_id = {
            rtype: {item["id"]: item for item in items if "id" in item}
            for rtype, items in inventory.items()
        }

    def items(self, rtype: str) -> list[dict]:
        return self.data.get(rtype) or []

    def name(self, rtype: str, ref: Any, item: dict | None = None, key: str = "") -> str | None:
        """Name of the object ``ref`` points to (export first, then summary_fields)."""
        if ref is None or ref == "":
            return None
        if isinstance(ref, dict):
            return ref.get("name")
        obj = self.by_id.get(rtype, {}).get(ref)
        if obj is not None:
            return obj.get("name")
        if item is not None and key:
            return ((item.get("summary_fields") or {}).get(key) or {}).get("name")
        return None


class _Builder:
    def __init__(self, export: _Export, org: dict) -> None:
        self.x = export
        self.org = org
        self.org_id = org["id"]
        self.org_name = org["name"]
        self.vault_vars: list[str] = []
        self.skipped: list[str] = []
        self.credential_ids: set[int] = set()

    # -- helpers -------------------------------------------------------------
    def _vault(self, *parts: str) -> str:
        var = "vault_" + "_".join(slug(p) for p in parts)
        if var not in self.vault_vars:
            self.vault_vars.append(var)
        return VaultRef("{{ " + var + " }}")

    def _scrub(self, value: Any, owner: str, path: str = "") -> Any:
        """Replace ``$encrypted$`` with placeholders and mark Jinja text unsafe."""
        if isinstance(value, dict):
            return {k: self._scrub(v, owner, f"{path}_{k}" if path else k) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub(v, owner, f"{path}_{i}") for i, v in enumerate(value)]
        if isinstance(value, VaultRef):
            return value
        if isinstance(value, str):
            if value == ENCRYPTED:
                return self._vault(owner, path or "secret")
            if "{{" in value or "{%" in value:
                return UnsafeText(value)
        return value

    def _vars(self, value: Any, owner: str) -> Any:
        """AAP stores variables as YAML/JSON text; emit a mapping when possible."""
        if isinstance(value, str):
            if not value.strip() or value.strip() in ("---", "{}"):
                return None
            try:
                parsed = yaml.safe_load(value)
            except yaml.YAMLError:
                parsed = None
            if isinstance(parsed, dict):
                return self._scrub(parsed, owner)
        return self._scrub(value, owner)

    @staticmethod
    def _compact(obj: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in obj.items() if v not in (None, "", [], {})}

    def _pick(self, item: dict, fields: tuple[str, ...]) -> dict[str, Any]:
        return {f: item.get(f) for f in fields}

    def _in_org(self, item: dict) -> bool:
        org = item.get("organization")
        return (org.get("id") if isinstance(org, dict) else org) == self.org_id

    def _cred_name(self, ref: Any, item: dict | None = None, key: str = "credential") -> str | None:
        if isinstance(ref, int):
            self.credential_ids.add(ref)
        return self.x.name("credentials", ref, item, key)

    def _ee(self, item: dict, key: str = "execution_environment") -> str | None:
        return self.x.name("execution_environments", item.get(key), item, key)

    # -- objects -------------------------------------------------------------
    def organizations(self) -> list[dict]:
        o = self.org
        return [self._compact({
            "name": o["name"],
            "description": o.get("description"),
            "max_hosts": o.get("max_hosts") or None,
            "default_environment": self._ee(o, "default_environment"),
        })]

    def teams(self) -> list[dict]:
        return [
            self._compact({"name": t["name"], "description": t.get("description"), "organization": self.org_name})
            for t in self.x.items("teams") if self._in_org(t)
        ]

    def projects(self) -> list[dict]:
        out = []
        for p in self.x.items("projects"):
            if not self._in_org(p):
                continue
            out.append(self._compact({
                "name": p["name"],
                "organization": self.org_name,
                **self._pick(p, _PROJECT_FIELDS),
                "local_path": p.get("local_path") if not p.get("scm_type") else None,
                "credential": self._cred_name(p.get("credential"), p),
                "signature_validation_credential": self._cred_name(
                    p.get("signature_validation_credential"), p, "signature_validation_credential"
                ),
                "default_environment": self._ee(p, "default_environment"),
            }))
        return out

    def inventories(self) -> tuple[list[dict], set[int]]:
        out, ids = [], set()
        for inv in self.x.items("inventories"):
            if not self._in_org(inv):
                continue
            ids.add(inv["id"])
            kind = inv.get("kind") or None
            out.append(self._compact({
                "name": inv["name"],
                "organization": self.org_name,
                "description": inv.get("description"),
                "kind": kind,
                "host_filter": inv.get("host_filter") if kind == "smart" else None,
                "variables": self._vars(inv.get("variables"), f"inventory_{inv['name']}"),
                "prevent_instance_group_fallback": inv.get("prevent_instance_group_fallback") or None,
            }))
        return out, ids

    def inventory_sources(self, inv_ids: set[int]) -> list[dict]:
        out = []
        for s in self.x.items("inventory_sources"):
            if s.get("inventory") not in inv_ids or s.get("source") == "constructed":
                continue
            out.append(self._compact({
                "name": s["name"],
                "inventory": self.x.name("inventories", s["inventory"]),
                "organization": self.org_name,
                **self._pick(s, _SOURCE_FIELDS),
                "source_project": self.x.name("projects", s.get("source_project"), s, "source_project"),
                "source_vars": self._vars(s.get("source_vars"), f"source_{s['name']}"),
                "credential": self._cred_name(s.get("credential"), s),
                "execution_environment": self._ee(s),
            }))
        return out

    def hosts(self, inv_ids: set[int]) -> list[dict]:
        return [
            self._compact({
                "name": h["name"],
                "inventory": self.x.name("inventories", h["inventory"]),
                "description": h.get("description"),
                "enabled": h.get("enabled"),
                "variables": self._vars(h.get("variables"), f"host_{h['name']}"),
            })
            for h in self.x.items("hosts") if h.get("inventory") in inv_ids
        ]

    def groups(self, inv_ids: set[int]) -> list[dict]:
        hosts_by_group: dict[int, list[str]] = {}
        for m in self.x.items("host_group_memberships"):
            hosts_by_group.setdefault(m.get("group_id"), []).append(m.get("host_name"))
        out = []
        for g in self.x.items("inventory_groups"):
            if g.get("inventory") not in inv_ids:
                continue
            children = [
                self.x.name("inventory_groups", c) if isinstance(c, int) else c
                for c in g.get("children") or []
            ]
            out.append(self._compact({
                "name": g["name"],
                "inventory": self.x.name("inventories", g["inventory"]),
                "description": g.get("description"),
                "variables": self._vars(g.get("variables"), f"group_{g['name']}"),
                "hosts": sorted({h for h in hosts_by_group.get(g["id"], []) if h}),
                "children": sorted({c for c in children if c}),
            }))
        return out

    def _labels(self, item: dict) -> list[str]:
        labels = ((item.get("summary_fields") or {}).get("labels") or {}).get("results") or []
        return sorted({lbl["name"] for lbl in labels if lbl.get("name")})

    def _credentials_list(self, item: dict) -> list[str]:
        ids = item.get("_credentials")
        summary = (item.get("summary_fields") or {}).get("credentials") or []
        if ids is None:
            ids = [c["id"] for c in summary if "id" in c]
        names = {c["id"]: c.get("name") for c in summary if "id" in c}
        result = []
        for cid in ids:
            self.credential_ids.add(cid)
            name = self.x.name("credentials", cid) or names.get(cid)
            if name:
                result.append(name)
        return sorted(set(result))

    def _survey(self, item: dict, owner: str) -> dict | None:
        spec = item.get("survey_spec")
        if not spec or not spec.get("spec"):
            return None
        spec = dict(spec)
        questions = []
        for q in spec.get("spec", []):
            q = dict(q)
            if q.get("type") == "password" and q.get("default") not in (None, ""):
                q["default"] = self._vault(owner, q.get("variable", "survey"))
            questions.append(q)
        spec["spec"] = questions
        return self._scrub(spec, owner)

    def templates(self, project_ids: set[int]) -> tuple[list[dict], set[int]]:
        out, ids = [], set()
        for jt in self.x.items("job_templates"):
            if not (self._in_org(jt) or (jt.get("organization") is None and jt.get("project") in project_ids)):
                continue
            ids.add(jt["id"])
            owner = f"jt_{jt['name']}"
            out.append(self._compact({
                "name": jt["name"],
                "organization": self.org_name,
                **self._pick(jt, _TEMPLATE_FIELDS),
                "inventory": self.x.name("inventories", jt.get("inventory"), jt, "inventory"),
                "project": self.x.name("projects", jt.get("project"), jt, "project"),
                "execution_environment": self._ee(jt),
                "credentials": self._credentials_list(jt),
                "labels": self._labels(jt),
                "extra_vars": self._vars(jt.get("extra_vars"), owner),
                "survey_spec": self._survey(jt, owner),
                "webhook_credential": self._cred_name(jt.get("webhook_credential"), jt, "webhook_credential"),
            }))
        return out, ids

    def workflows(self) -> tuple[list[dict], set[int]]:
        out, ids = [], set()
        for wf in self.x.items("workflow_job_templates"):
            if not self._in_org(wf):
                continue
            ids.add(wf["id"])
            owner = f"wf_{wf['name']}"
            out.append(self._compact({
                "name": wf["name"],
                "organization": self.org_name,
                **self._pick(wf, _WORKFLOW_FIELDS),
                "inventory": self.x.name("inventories", wf.get("inventory"), wf, "inventory"),
                "labels": self._labels(wf),
                "extra_vars": self._vars(wf.get("extra_vars"), owner),
                "survey_spec": self._survey(wf, owner),
                "simplified_workflow_nodes": self._nodes(wf),
            }))
        return out, ids

    def _nodes(self, wf: dict) -> list[dict]:
        nodes = wf.get("nodes") or []
        ident = {n["id"]: n.get("identifier") or f"node{n['id']}" for n in nodes if "id" in n}
        out = []
        for n in nodes:
            ujt = (n.get("summary_fields") or {}).get("unified_job_template") or {}
            node: dict[str, Any] = {"identifier": ident.get(n.get("id"))}
            if ujt.get("unified_job_type") == "workflow_approval":
                node["approval_node"] = self._compact({
                    "name": ujt.get("name"),
                    "description": ujt.get("description"),
                    "timeout": ujt.get("timeout"),
                })
            else:
                name = ujt.get("name")
                if not name:
                    self.skipped.append(f"workflow '{wf['name']}' node {node['identifier']}: template unknown")
                node["unified_job_template"] = name
                if ujt.get("unified_job_type") in _UJT_TYPES:
                    node["type"] = _UJT_TYPES[ujt["unified_job_type"]]
            node.update({
                "all_parents_must_converge": n.get("all_parents_must_converge"),
                "success_nodes": [ident[i] for i in n.get("success_nodes") or [] if i in ident],
                "failure_nodes": [ident[i] for i in n.get("failure_nodes") or [] if i in ident],
                "always_nodes": [ident[i] for i in n.get("always_nodes") or [] if i in ident],
                "extra_data": self._scrub(n.get("extra_data"), f"wf_{wf['name']}_{node['identifier']}"),
                "inventory": self.x.name("inventories", n.get("inventory"), n, "inventory"),
                "limit": n.get("limit"),
            })
            out.append({k: v for k, v in node.items() if v is not None and v != {}})
        return out

    def schedules(self, owners: dict[str, set[int]]) -> list[dict]:
        out = []
        for s in self.x.items("schedules"):
            ujt = (s.get("summary_fields") or {}).get("unified_job_template") or {}
            kind = _UJT_TYPES.get(ujt.get("unified_job_type", ""))
            if kind is None or s.get("unified_job_template") not in owners.get(kind, set()):
                continue
            out.append(self._compact({
                "name": s["name"],
                "unified_job_template": ujt.get("name"),
                "organization": self.org_name,
                **self._pick(s, _SCHEDULE_FIELDS),
                "inventory": self.x.name("inventories", s.get("inventory"), s, "inventory"),
                "execution_environment": self._ee(s),
                "extra_data": self._scrub(s.get("extra_data"), f"schedule_{s['name']}"),
            }))
        return out

    def credentials(self) -> tuple[list[dict], set[int]]:
        """Org credentials plus global ones referenced by this org's objects."""
        out, type_ids = [], set()
        for c in self.x.items("credentials"):
            org_owned = self._in_org(c)
            if not (org_owned or (c.get("organization") is None and c["id"] in self.credential_ids)):
                continue
            ctype = self.x.by_id.get("credential_types", {}).get(c.get("credential_type"), {})
            type_name = ctype.get("name") or self.x.name("credential_types", c.get("credential_type"), c, "credential_type")
            type_ids.add(c.get("credential_type"))
            secret_fields = {
                f["id"] for f in (ctype.get("inputs") or {}).get("fields", []) if f.get("secret")
            }
            inputs = {}
            for key, value in (c.get("inputs") or {}).items():
                if key in secret_fields or value == ENCRYPTED:
                    inputs[key] = self._vault(c["name"], key)
                else:
                    inputs[key] = self._scrub(value, f"cred_{c['name']}", key)
            out.append(self._compact({
                "name": c["name"],
                "description": c.get("description"),
                "organization": self.org_name if org_owned else None,
                "credential_type": type_name,
                "inputs": inputs,
            }))
        return out, type_ids

    def credential_types(self, type_ids: set[int]) -> list[dict]:
        return [
            self._compact({
                "name": t["name"],
                "description": t.get("description"),
                "kind": t.get("kind"),
                "inputs": self._scrub(t.get("inputs"), f"ctype_{t['name']}"),
                "injectors": self._scrub(t.get("injectors"), f"ctype_{t['name']}"),
            })
            for t in self.x.items("credential_types")
            if t.get("id") in type_ids and not t.get("managed") and not t.get("managed_by_tower")
        ]


def _find_org(export: _Export, organization: str) -> dict:
    orgs = export.items("organizations")
    exact = [o for o in orgs if o.get("name") == organization]
    matches = exact or [o for o in orgs if str(o.get("name", "")).lower() == organization.lower()]
    if not matches:
        available = ", ".join(sorted(o.get("name", "?") for o in orgs)[:10])
        raise ValueError(f"Organization '{organization}' not in the export. Available: {available}")
    return matches[0]


def export_organization(export_dir: Path, organization: str) -> CaCResult:
    """Build the ``infra.aap_configuration`` variables for one organization."""
    export = _Export(load_inventory_from_export(export_dir))
    b = _Builder(export, _find_org(export, organization))

    inventories, inv_ids = b.inventories()
    projects = b.projects()
    project_ids = {p["id"] for p in export.items("projects") if b._in_org(p)}
    templates, jt_ids = b.templates(project_ids)
    workflows, wf_ids = b.workflows()
    sources = b.inventory_sources(inv_ids)
    source_ids = {s["id"] for s in export.items("inventory_sources") if s.get("inventory") in inv_ids}
    schedules = b.schedules({
        "job_template": jt_ids,
        "workflow_job_template": wf_ids,
        "project": project_ids,
        "inventory_source": source_ids,
    })
    hosts = b.hosts(inv_ids)
    groups = b.groups(inv_ids)
    teams = b.teams()
    # Credentials last: projects, sources and templates register the ones they use.
    credentials, type_ids = b.credentials()

    variables = {
        "aap_organizations": b.organizations(),
        "aap_teams": teams,
        "controller_credential_types": b.credential_types(type_ids),
        "controller_credentials": credentials,
        "controller_projects": projects,
        "controller_inventories": inventories,
        "controller_inventory_sources": sources,
        "controller_hosts": hosts,
        "controller_groups": groups,
        "controller_templates": templates,
        "controller_workflows": workflows,
        "controller_schedules": schedules,
    }
    for items in variables.values():
        items.sort(key=lambda o: (str(o.get("inventory", "")), str(o.get("name", ""))))
    return CaCResult(b.org_name, variables, sorted(b.vault_vars), b.skipped)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


def write_cac(result: CaCResult, out_dir: Path) -> list[Path]:
    """Write one ``<var>.yml`` per non-empty variable, ``vault_template.yml`` and README.md."""
    target = out_dir / slug(result.organization)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    header = (
        f"# Generated by aap-migrate cac-export for organization '{result.organization}'.\n"
        "# infra.aap_configuration format; secrets are {{ vault_* }} placeholders.\n"
    )
    for var in VAR_NAMES:
        items = result.variables.get(var) or []
        path = target / f"{var}.yml"
        if not items:
            path.unlink(missing_ok=True)
            continue
        path.write_text(header + "---\n" + dump_yaml({var: items}), encoding="utf-8")
        written.append(path)

    vault = target / "vault_template.yml"
    vault.write_text(
        "# Fill in and encrypt: ansible-vault encrypt vault_template.yml\n"
        "# Never commit this file with real values.\n---\n"
        + dump_yaml({v: "" for v in result.vault_vars} or {}),
        encoding="utf-8",
    )
    written.append(vault)

    readme = target / "README.md"
    readme.write_text(_readme(result), encoding="utf-8")
    written.append(readme)
    return written


def _readme(result: CaCResult) -> str:
    rows = "\n".join(f"| `{k}` | {v} |" for k, v in result.counts().items() if v)
    skipped = "".join(f"\n- {s}" for s in result.skipped) or "\n- none"
    return f"""# Configuration as code: {result.organization}

Generated from the staged AAP 2.4 export by `aap-migrate cac-export`, for the
`infra.aap_configuration` collection. References use names, never IDs.

| Variable | Objects |
| --- | ---: |
{rows}

## Secrets

Every secret is a `{{{{ vault_* }}}}` placeholder ({len(result.vault_vars)} in total). Fill in
`vault_template.yml`, then encrypt it with `ansible-vault encrypt vault_template.yml`.

## Apply to AAP 2.6

```yaml
- hosts: localhost
  connection: local
  gather_facts: false
  vars_files:
    - vault_template.yml
  pre_tasks:
    - ansible.builtin.include_vars:
        dir: .
        files_matching: '^(aap|controller)_.*\\.yml$'
  roles:
    - infra.aap_configuration.dispatch
```

Run it with `aap_hostname` and `aap_token` set (or `AAP_HOSTNAME` / `AAP_TOKEN` in the environment).

## Not exported

Users, role assignments, notification templates, instance groups and execution
environments are not exported. Users and roles move to the Platform Gateway; review them separately.

Skipped:{skipped}
"""

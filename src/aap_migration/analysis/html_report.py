"""Self-contained HTML UI for the cross-organization Dependency Analyzer.

The page answers two questions for whoever plans the migration:

1. *In which order can organizations move?*  Organizations are laid out in
   migration waves (phases computed from the dependency graph); each wave can
   be migrated in parallel once the previous ones are done.
2. *What will break on the way?*  When a pre-migration report
   (``aap-bridge premigration-report``) is supplied, every organization,
   dependency and dependent resource is annotated with its predicted outcome
   (Will fail / At risk / Needs action / Stale / Ready), so a dependency that
   is itself going to fail is visible as a blocker for every organization
   that needs it.

All CSS and JavaScript are inline (air-gapped friendly).
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

from aap_migration.reporting.ui_kit import page

if TYPE_CHECKING:
    from aap_migration.analysis.dependency_analyzer import GlobalDependencyReport


_RESOURCE_TYPE_DISPLAY = {
    "job_templates": "Job Templates",
    "workflow_job_templates": "Workflow Templates",
    "projects": "Projects",
    "inventories": "Inventories",
    "inventory_sources": "Inventory Sources",
    "credentials": "Credentials",
    "teams": "Teams",
    "schedules": "Schedules",
    "notification_templates": "Notifications",
    "hosts": "Hosts",
    "inventory_groups": "Groups",
    "credential_input_sources": "Credential Inputs",
}


def _display_type(rtype: str) -> str:
    """Friendly label for a resource type."""
    return _RESOURCE_TYPE_DISPLAY.get(rtype, rtype.replace("_", " ").title())


# ---------------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------------
def build_view_model(report: GlobalDependencyReport) -> dict[str, Any]:
    """Convert a GlobalDependencyReport into the plain dict rendered by the UI."""
    orgs = []
    for org_name, org_report in report.org_reports.items():
        summary = [
            {"type": rtype, "count": len(items)}
            for rtype, items in (org_report.resources or {}).items()
            if items
        ]
        deps = []
        for dep_org, dep_list in sorted(org_report.dependencies.items()):
            deps.append({
                "org": dep_org,
                "resources": sorted(
                    (
                        {
                            "type": d.resource_type,
                            "id": d.resource_id,
                            "name": d.resource_name,
                            "used_by": [
                                {"type": u.get("type"), "id": u.get("id"), "name": u.get("name")}
                                for u in d.required_by
                            ],
                        }
                        for d in dep_list
                    ),
                    key=lambda r: (r["type"], str(r["name"]).lower()),
                ),
            })
        orgs.append({
            "name": org_name,
            "id": org_report.org_id,
            "total_resources": org_report.resource_count,
            "has_dependencies": org_report.has_cross_org_deps,
            "required_before": list(org_report.required_migrations_before),
            "resource_summary": sorted(summary, key=lambda s: -s["count"]),
            "dependencies": deps,
        })
    return {
        "analysis_date": report.analysis_date.isoformat(timespec="seconds"),
        "source_url": str(report.source_url),
        "total_organizations": report.total_organizations,
        "independent_orgs": list(report.independent_orgs),
        "dependent_orgs": list(report.dependent_orgs),
        "migration_order": list(report.migration_order),
        "phases": [
            {
                "phase": p.get("phase"),
                "description": p.get("description", ""),
                "orgs": list(p.get("orgs", [])),
                "has_cycle": p.get("has_cycle", False),
            }
            for p in report.migration_phases
        ],
        "cycles": [list(c) for c in (getattr(report, "cycles", None) or [])],
        "organizations": sorted(orgs, key=lambda o: o["name"].lower()),
    }


def view_model_from_json(data: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the view model from ``analyze-dependencies --output-json``.

    Lets the UI be re-rendered (for example after a new pre-migration report)
    without querying AAP 2.4 again.
    """
    orgs = []
    for name, o in (data.get("organizations") or {}).items():
        deps = []
        for dep_org, items in sorted((o.get("dependencies") or {}).items()):
            deps.append({
                "org": dep_org,
                "resources": [
                    {
                        "type": i.get("resource_type"),
                        "id": i.get("resource_id"),
                        "name": i.get("resource_name"),
                        "used_by": [
                            {"type": u.get("type"), "id": u.get("id"), "name": u.get("name")}
                            for u in (i.get("used_by") or [])
                        ],
                    }
                    for i in items
                ],
            })
        orgs.append({
            "name": name,
            "id": o.get("org_id"),
            "total_resources": o.get("resource_count", 0),
            "has_dependencies": o.get("has_dependencies", False),
            "required_before": o.get("required_before", []),
            "resource_summary": o.get("resource_summary", []),
            "dependencies": deps,
        })
    return {
        "analysis_date": data.get("analysis_date", ""),
        "source_url": data.get("source_url", ""),
        "total_organizations": data.get("total_organizations", len(orgs)),
        "independent_orgs": data.get("independent_orgs", []),
        "dependent_orgs": data.get("dependent_orgs", []),
        "migration_order": data.get("migration_order", []),
        "phases": [
            {
                "phase": p.get("phase"),
                "description": p.get("description", ""),
                "orgs": p.get("orgs", []),
                "has_cycle": p.get("has_cycle", False),
            }
            for p in data.get("migration_phases", [])
        ],
        "cycles": data.get("cycles", []),
        "organizations": sorted(orgs, key=lambda o: o["name"].lower()),
    }


def slim_risk(premigration: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only what the dependency UI needs from a pre-migration report."""
    if not premigration:
        return None
    titles: dict[str, list] = {}
    for f in premigration.get("findings", []):
        titles.setdefault(f["key"], []).append([f["rc"], f["severity"], f["title"]])
    resources = {}
    for key, r in (premigration.get("resources") or {}).items():
        resources[key] = {
            "o": r.get("outcome"),
            "n": r.get("name"),
            "org": r.get("organization"),
            "s": bool(r.get("stale")),
            "f": titles.get(key, [])[:6],
        }
    return {
        "generated": premigration.get("generated"),
        "summary": {
            k: premigration.get("summary", {}).get(k)
            for k in ("total_resources", "outcomes", "predicted_success_pct", "resources_by_rc")
        },
        "rc_titles": {rc: c.get("title") for rc, c in (premigration.get("rc_catalog") or {}).items()},
        "organizations": {
            n: {
                "outcomes": o.get("outcomes", {}),
                "predicted_success_pct": o.get("predicted_success_pct"),
                "rc": o.get("rc", {}),
                "total": o.get("total", 0),
            }
            for n, o in (premigration.get("organizations") or {}).items()
        },
        "resources": resources,
    }


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
_BODY = """
<header class="masthead">
  <div class="wrap">
    <div class="brand-bar" aria-hidden="true"></div>
    <div class="titles">
      <h1>Migration Dependency Map</h1>
      <p class="assessment-subtitle">Migration AAP __SRC_VERSION__ → 2.6 on OpenShift<span class="sep">·</span>Source AAP __SRC_VERSION__: __SOURCE__<span class="sep">·</span>Date: __DATE__<span class="sep">·</span><span id="riskStamp"></span></p>
    </div>
    <button class="theme-btn" id="themeBtn" type="button">◐ Theme</button>
  </div>
</header>
<main class="wrap">
  <section class="success-section">
    <div class="success-grid">
      <div class="success-pct-block">
        <div class="pct" id="headline"></div>
        <div class="pct-sub" id="headlineSub"></div>
      </div>
      <div class="success-detail">
        <p class="summary-line" id="headlineText"></p>
        <div class="outcome-row" id="headlineOutcomes"></div>
      </div>
    </div>
    <div id="riskStrip" style="margin-top:16px"></div>
    <div id="cycleAlert"></div>
  </section>

  <section class="stat-cards" id="depStatCards" aria-label="Key numbers" style="margin-bottom:8px"></section>

  <nav class="tabs" role="tablist" aria-label="Sections">
    <button role="tab" data-tab="waves">Migration waves</button>
    <button role="tab" data-tab="orgs">Organizations<span class="count" id="cOrgs"></span></button>
    <button role="tab" data-tab="links">Cross-org links<span class="count" id="cLinks"></span></button>
    <button role="tab" data-tab="blockers" id="tabBlockers" hidden>Blockers<span class="count" id="cBlockers"></span></button>
  </nav>

  <section class="panel" id="panel-waves" role="tabpanel">
    <p class="section-desc">Organizations in the same wave can be migrated in parallel. A wave starts when every previous wave is done. Select an organization to see what it needs and what may break.</p>
    <div class="waves" id="waves"></div>
  </section>

  <section class="panel" id="panel-orgs" role="tabpanel" hidden>
    <div class="orgs-layout">
      <aside class="org-list">
        <div class="field"><label for="oSearch">Find organization</label><input type="search" id="oSearch" placeholder="Name"></div>
        <div class="chips" id="oFilter" role="group" aria-label="Filter organizations"></div>
        <div class="field"><label for="oSort">Sort by</label><select id="oSort">
          <option value="name">Name</option><option value="wave">Wave</option><option value="size">Objects</option>
          <option value="deps">Dependencies</option><option value="risk">Failing objects</option></select></div>
        <ul id="orgItems"></ul>
      </aside>
      <div id="orgDetail" class="org-detail"><div class="empty">Select an organization on the left.</div></div>
    </div>
  </section>

  <section class="panel" id="panel-links" role="tabpanel" hidden>
    <p class="section-desc">Every cross-organization dependency: resources owned by one organization but used by another. These links determine migration order.</p>
    <div class="toolbar">
      <div class="field"><label for="lSearch">Search</label><input type="search" id="lSearch" placeholder="Organization or resource"></div>
      <div class="field"><label for="lType">Resource type</label><select id="lType"></select></div>
      <div class="field" id="lRiskField" hidden><label for="lRisk">Predicted outcome</label><select id="lRisk"></select></div>
      <button class="btn" type="button" id="lCsv">↓ CSV</button>
      <span class="result-count" id="lCount"></span>
    </div>
    <div id="linksTable"></div>
  </section>

  <section class="panel" id="panel-blockers" role="tabpanel" hidden>
    <p class="section-desc">Shared objects predicted to fail or at risk that block other organizations. Fix these first.</p>
    <div id="blockersTable"></div>
  </section>
</main>
"""

_EXTRA_CSS = """
<style>
.waves { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(250px, 1fr); gap: 0; overflow-x: auto; padding-bottom: 8px; }
.wave { padding: 0 16px; border-left: 1px solid var(--line); min-width: 250px; }
.wave:first-child { border-left: 0; padding-left: 0; }
.wave-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }
.wave-num { font-family: "Red Hat Display", system-ui, sans-serif; font-size: 2rem; font-weight: 700; line-height: 1; color: var(--ink-3); }
.wave-sub { color: var(--ink-3); font-size: 0.85rem; margin-bottom: 12px; min-height: 2.6em; }
.org-chip { display: block; width: 100%; text-align: left; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-m); padding: 9px 11px 10px; margin-bottom: 8px; cursor: pointer; }
.org-chip:hover { border-color: var(--ink-3); }
.org-chip .nm { font-weight: 600; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.org-chip .mt { color: var(--ink-3); font-size: 0.82rem; }
.org-chip .mini { display: flex; height: 6px; border-radius: 3px; overflow: hidden; margin-top: 7px; background: var(--line-2); }
.org-chip .mini span { height: 100%; }
.org-chip.cycle { border-style: dashed; border-color: var(--at_risk); }
.alert { border-left: 4px solid var(--at_risk); background: var(--at_risk-bg); padding: 10px 14px; border-radius: var(--radius-s); margin-top: 16px; }
.alert strong { color: var(--ink); }
.orgs-layout { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 28px; align-items: start; }
@media (max-width: 900px) { .orgs-layout { grid-template-columns: minmax(0, 1fr); } }
.org-list { position: sticky; top: 12px; display: flex; flex-direction: column; gap: 12px; }
.org-list .field input, .org-list .field select { min-width: 0; width: 100%; }
.org-list ul { list-style: none; margin: 0; padding: 0; max-height: 60vh; overflow-y: auto; border: 1px solid var(--line); border-radius: var(--radius-m); background: var(--surface); }
.org-list li button { all: unset; box-sizing: border-box; display: flex; justify-content: space-between; gap: 8px; width: 100%; padding: 8px 12px; cursor: pointer; border-bottom: 1px solid var(--line-2); }
.org-list li button:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; }
.org-list li button[aria-current="true"] { background: var(--line-2); font-weight: 600; }
.org-list li:last-child button { border-bottom: 0; }
.org-list .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 8px; flex: none; transform: translateY(-1px); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips button { border: 1px solid var(--line); background: var(--surface); border-radius: 999px; padding: 3px 10px; cursor: pointer; font-size: 0.85rem; }
.chips button[aria-pressed="true"] { background: var(--ink); color: var(--surface); border-color: var(--ink); }
.org-detail h2 { font-size: 1.5rem; }
.kpis { display: flex; flex-wrap: wrap; gap: 10px 34px; margin: 16px 0 20px; }
.kpis div { display: flex; flex-direction: column; }
.kpis .v { font-size: 1.5rem; font-weight: 700; font-family: "Red Hat Display", system-ui, sans-serif; }
.kpis .k { color: var(--ink-3); font-size: 0.85rem; }
.linkchips button { border: 1px solid var(--line); background: var(--surface); border-radius: var(--radius-s); padding: 3px 9px; margin: 0 6px 6px 0; cursor: pointer; }
.linkchips button:hover { border-color: var(--ink-3); }
details.dep { border: 1px solid var(--line); border-radius: var(--radius-m); background: var(--surface); margin-bottom: 10px; }
details.dep > summary { cursor: pointer; padding: 10px 14px; display: flex; gap: 12px; align-items: center; justify-content: space-between; list-style: none; }
details.dep > summary::-webkit-details-marker { display: none; }
details.dep > summary::before { content: "+"; font-weight: 700; color: var(--ink-3); width: 1em; }
details.dep[open] > summary::before { content: "\\2212"; }
details.dep > summary .grow { flex: 1; font-weight: 600; }
details.dep .inner { padding: 0 14px 12px; }
.deprow { border-top: 1px solid var(--line-2); padding: 9px 0; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 12px; }
.deprow .used { grid-column: 1 / -1; color: var(--ink-2); font-size: 0.86rem; }
.deprow .used li { margin: 2px 0; }
.deprow ul { margin: 4px 0 0; padding-left: 18px; }
.why { font-size: 0.84rem; color: var(--ink-2); grid-column: 1 / -1; }
</style>
"""

_SCRIPT = r"""
const ORGS = DEPS.organizations;
const byName = {}; ORGS.forEach(o => byName[o.name] = o);
const waveOf = {}; DEPS.phases.forEach(p => p.orgs.forEach(n => waveOf[n] = p.phase));
const inCycle = new Set(); (DEPS.cycles || []).forEach(c => c.forEach(n => inCycle.add(n)));
const neededBy = {}; ORGS.forEach(o => o.required_before.forEach(d => (neededBy[d] = neededBy[d] || []).push(o.name)));
const HAS_RISK = !!RISK;
const R = HAS_RISK ? RISK.resources : {};
function risk(type, id) { return R[type + ':' + id]; }
function outcomeOf(type, id) { const r = risk(type, id); return r ? r.o : (HAS_RISK ? 'ready' : null); }
function orgRisk(name) { return HAS_RISK ? (RISK.organizations[name] || null) : null; }
function failing(name) { const r = orgRisk(name); return r ? (r.outcomes.will_fail || 0) : 0; }
function mini(name) {
  const r = orgRisk(name); if (!r) return '';
  return '<div class="mini">' + OUTCOMES.filter(k => r.outcomes[k]).map(k => '<span style="flex:' + r.outcomes[k] + ';background:var(--' + k + ')"></span>').join('') + '</div>';
}
function orgDot(name) {
  const r = orgRisk(name); if (!r) return '';
  const o = r.outcomes.will_fail ? 'will_fail' : r.outcomes.at_risk ? 'at_risk' : r.outcomes.needs_action ? 'needs_action' : 'ready';
  return '<span class="dot" style="background:var(--' + o + ')" title="' + OUTCOME_LABEL[o] + '"></span>';
}

/* All cross-org edges, flattened */
const LINKS = [];
ORGS.forEach(o => o.dependencies.forEach(d => d.resources.forEach(r => LINKS.push({
  org: o.name, from: d.org, type: r.type, id: r.id, name: r.name, used_by: r.used_by, uses: r.used_by.length,
  outcome: outcomeOf(r.type, r.id)
}))));

function header() {
  const waves = DEPS.phases.length;
  const ind = DEPS.independent_orgs.length, dep = DEPS.dependent_orgs.length;
  $('#headline').textContent = waves + (waves === 1 ? ' wave' : ' waves');
  $('#headlineSub').textContent = 'migration plan';
  let txt = fmtNum(ORGS.length) + ' organizations to migrate. ' + fmtNum(ind) + ' independent, ' + fmtNum(dep) +
    ' share objects through ' + fmtNum(LINKS.length) + ' cross-org links.';
  if (HAS_RISK) {
    txt += ' Predicted first-pass success: ' + RISK.summary.predicted_success_pct + '%.';
    const out = RISK.summary.outcomes;
    const items = [{k:'will_fail',l:'Will fail'},{k:'at_risk',l:'At risk'},{k:'needs_action',l:'Needs action'},{k:'stale',l:'Stale'},{k:'ready',l:'Ready'}];
    $('#headlineOutcomes').innerHTML = items.map(i =>
      '<div class="outcome-item"><span class="o-dot" style="background:var(--' + i.k + ')"></span><span class="o-val">' + fmtNum(out[i.k] || 0) + '</span><span class="o-lbl">' + i.l + '</span></div>'
    ).join('');
    $('#riskStamp').textContent = 'Pre-migration data: ' + String(RISK.generated || '').slice(0, 16).replace('T', ' ');
    $('#riskStrip').innerHTML = '<div class="strip" id="strip"></div><div class="legend" id="legend"></div>';
    renderStrip($('#strip'), out); renderLegend($('#legend'), out);
  } else {
    $('#riskStamp').textContent = 'No pre-migration data';
  }
  $('#headlineText').textContent = txt;
  const cards = [
    {v: fmtNum(ORGS.length), l: 'Organizations'},
    {v: fmtNum(waves), l: 'Waves'},
    {v: fmtNum(ind), l: 'Independent'},
    {v: fmtNum(dep), l: 'With dependencies'},
    {v: fmtNum(LINKS.length), l: 'Cross-org links'},
  ];
  if (HAS_RISK) cards.push({v: RISK.summary.predicted_success_pct + '%', l: 'First-pass success'});
  $('#depStatCards').innerHTML = cards.map(c =>
    '<div class="stat-card"><div class="stat-value">' + c.v + '</div><div class="stat-label">' + c.l + '</div></div>'
  ).join('');
  if (DEPS.cycles && DEPS.cycles.length) {
    $('#cycleAlert').innerHTML = '<div class="alert"><strong>' + DEPS.cycles.length + ' circular dependenc' + (DEPS.cycles.length === 1 ? 'y' : 'ies') + '.</strong> ' +
      DEPS.cycles.map(c => esc(c.join(', '))).join('; ') + '. These organizations must move together.</div>';
  }
  $('#cOrgs').textContent = fmtNum(ORGS.length); $('#cLinks').textContent = fmtNum(LINKS.length);
}

function wavesTab() {
  $('#waves').innerHTML = DEPS.phases.map(p => {
    const orgs = p.orgs.slice().sort((a, b) => failing(b) - failing(a) || a.localeCompare(b));
    const fail = orgs.reduce((a, n) => a + failing(n), 0);
    return '<section class="wave"><div class="wave-head"><span class="wave-num">' + p.phase + '</span><h3>Wave ' + p.phase + '</h3></div>' +
      '<div class="wave-sub">' + fmtNum(orgs.length) + ' organization' + (orgs.length === 1 ? '' : 's') +
      (HAS_RISK && fail ? ', <strong style="color:var(--will_fail)">' + fmtNum(fail) + ' objects will fail</strong>' : '') +
      (p.has_cycle ? '<br>includes a circular dependency' : '') + '</div>' +
      orgs.map(n => { const o = byName[n] || {total_resources: 0, required_before: []};
        const meta = fmtNum(o.total_resources) + ' objects' + (o.required_before.length ? ', needs ' + o.required_before.length : '') +
          (HAS_RISK && failing(n) ? ', ' + failing(n) + ' will fail' : '');
        return '<button class="org-chip' + (inCycle.has(n) ? ' cycle' : '') + '" data-org="' + esc(n) + '" type="button"><span class="nm">' + esc(n) + '</span><span class="mt">' + meta + '</span>' + mini(n) + '</button>';
      }).join('') + '</section>';
  }).join('');
  $all('.org-chip').forEach(b => b.addEventListener('click', () => selectOrg(b.dataset.org)));
}

/* Organizations */
let orgFilter = 'all', current = null;
function orgsTab() {
  const filters = [['all', 'All'], ['independent', 'Standalone'], ['dependent', 'Has dependencies']];
  if (HAS_RISK) filters.push(['failing', 'Has failures']);
  $('#oFilter').innerHTML = filters.map(([k, l]) => '<button type="button" data-f="' + k + '" aria-pressed="' + (k === orgFilter) + '">' + l + '</button>').join('');
  $all('#oFilter button').forEach(b => b.addEventListener('click', () => { orgFilter = b.dataset.f; $all('#oFilter button').forEach(x => x.setAttribute('aria-pressed', String(x === b))); listOrgs(); }));
  if (!HAS_RISK) $('#oSort option[value="risk"]').remove();
  $('#oSearch').addEventListener('input', listOrgs); $('#oSort').addEventListener('input', listOrgs);
  listOrgs();
}
function listOrgs() {
  const q = $('#oSearch').value.trim().toLowerCase(), sort = $('#oSort').value;
  let list = ORGS.filter(o => (!q || o.name.toLowerCase().includes(q)) &&
    (orgFilter === 'all' || (orgFilter === 'independent' && !o.has_dependencies) || (orgFilter === 'dependent' && o.has_dependencies) || (orgFilter === 'failing' && failing(o.name))));
  const keyf = {name: o => o.name.toLowerCase(), wave: o => waveOf[o.name] || 0, size: o => -o.total_resources, deps: o => -o.required_before.length, risk: o => -failing(o.name)}[sort];
  list.sort((a, b) => { const x = keyf(a), y = keyf(b); return x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name); });
  $('#orgItems').innerHTML = list.length ? list.map(o => '<li><button type="button" data-org="' + esc(o.name) + '" aria-current="' + (o.name === current) + '"><span>' + orgDot(o.name) + esc(o.name) + '</span><span class="muted small">wave ' + (waveOf[o.name] || '?') + '</span></button></li>').join('')
    : '<li><div class="empty">No organization matches.</div></li>';
  $all('#orgItems button').forEach(b => b.addEventListener('click', () => selectOrg(b.dataset.org, true)));
}
function selectOrg(name, stay) {
  current = name; if (!stay) showTab('orgs', true); listOrgs();
  const o = byName[name]; if (!o) { $('#orgDetail').innerHTML = '<div class="empty">No details for ' + esc(name) + '.</div>'; return; }
  const r = orgRisk(name), nb = (neededBy[name] || []).sort();
  let h = '<p class="muted small">Wave ' + (waveOf[name] || '?') + (inCycle.has(name) ? ', part of a circular dependency' : '') + '</p><h2>' + esc(name) + '</h2>';
  h += '<p style="margin-top:6px">' + (o.has_dependencies ? 'Uses objects owned by ' + o.required_before.length + ' other organization' + (o.required_before.length === 1 ? '' : 's') + ': migrate those first.' : 'Self-contained: can be migrated on its own.') +
    (nb.length ? ' ' + nb.length + ' organization' + (nb.length === 1 ? ' depends' : 's depend') + ' on it.' : '') + '</p>';
  h += '<div class="kpis"><div><span class="v">' + fmtNum(o.total_resources) + '</span><span class="k">objects</span></div>' +
    '<div><span class="v">' + o.required_before.length + '</span><span class="k">depends on</span></div><div><span class="v">' + nb.length + '</span><span class="k">needed by</span></div>';
  if (r) h += '<div><span class="v" style="color:var(--will_fail)">' + fmtNum(r.outcomes.will_fail || 0) + '</span><span class="k">will fail</span></div>' +
    '<div><span class="v">' + fmtNum(r.outcomes.at_risk || 0) + '</span><span class="k">at risk</span></div><div><span class="v">' + r.predicted_success_pct + '%</span><span class="k">first-pass success</span></div>';
  h += '</div>';
  if (r) { h += '<div class="strip" id="orgStrip" style="height:14px"></div><div class="legend" id="orgLegend"></div>'; }
  if (o.required_before.length) h += '<h3 style="margin-top:24px">Migrate these first</h3><div class="linkchips" style="margin-top:8px">' + o.required_before.map(n => '<button type="button" data-org="' + esc(n) + '">' + orgDot(n) + esc(n) + '</button>').join('') + '</div>';
  if (nb.length) h += '<h3 style="margin-top:18px">Waiting for this organization</h3><div class="linkchips" style="margin-top:8px">' + nb.map(n => '<button type="button" data-org="' + esc(n) + '">' + orgDot(n) + esc(n) + '</button>').join('') + '</div>';

  if (o.dependencies.length) {
    h += '<h3 style="margin:26px 0 10px">Objects borrowed from other organizations</h3>';
    o.dependencies.forEach(d => {
      const bad = d.resources.filter(x => ['will_fail', 'at_risk'].includes(outcomeOf(x.type, x.id))).length;
      const badCls = d.resources.some(x => outcomeOf(x.type, x.id) === 'will_fail') ? 'will_fail' : 'at_risk';
      h += '<details class="dep"' + (bad ? ' open' : '') + '><summary><span class="grow">' + esc(d.org) + '</span><span class="muted small">' + d.resources.length + ' object' + (d.resources.length === 1 ? '' : 's') + '</span>' +
        (bad ? '<span class="pill o-' + badCls + '">' + bad + ' blocking</span>' : '') + '</summary><div class="inner">';
      d.resources.forEach(x => {
        const ro = outcomeOf(x.type, x.id), rr = risk(x.type, x.id);
        h += '<div class="deprow"><div><strong>' + esc(x.name) + '</strong> <span class="muted small">' + esc(typeLabel(x.type)) + ' #' + esc(x.id) + '</span></div><div>' + (ro ? pill(ro) : '') + '</div>';
        if (rr && rr.f.length) h += '<div class="why">' + rr.f.filter(f => f[0] !== 'STALE' || rr.f.length === 1).map(f => '<span class="rc">' + esc(f[0]) + '</span> ' + esc(f[2])).join('; ') + '</div>';
        h += '<div class="used">Used here by<ul>' + x.used_by.map(u => { const uo = outcomeOf(u.type, u.id);
          return '<li>' + esc(u.name) + ' <span class="muted">' + esc(typeLabel(u.type)) + ' #' + esc(u.id) + '</span> ' + (uo && uo !== 'ready' ? pill(uo) : '') + '</li>'; }).join('') + '</ul></div></div>';
      });
      h += '</div></details>';
    });
  }

  h += '<h3 style="margin:26px 0 10px">What it contains</h3><div class="table-wrap" style="max-height:none"><table class="data"><thead><tr><th>Type</th><th class="num">Objects</th></tr></thead><tbody>' +
    (o.resource_summary.length ? o.resource_summary.map(s => '<tr><td>' + esc(typeLabel(s.type)) + '</td><td class="num">' + fmtNum(s.count) + '</td></tr>').join('') : '<tr><td colspan="2" class="muted">No objects found.</td></tr>') + '</tbody></table></div>';

  if (HAS_RISK) {
    const items = Object.entries(R).filter(([k, v]) => v.org === name && ['will_fail', 'at_risk'].includes(v.o)).sort((a, b) => (a[1].o === 'will_fail' ? 0 : 1) - (b[1].o === 'will_fail' ? 0 : 1));
    h += '<h3 style="margin:26px 0 10px">Objects in this organization that will fail or are at risk</h3>';
    h += items.length ? '<div class="table-wrap"><table class="data"><thead><tr><th>Outcome</th><th>Object</th><th>Why</th></tr></thead><tbody>' +
      items.slice(0, 200).map(([k, v]) => '<tr><td>' + pill(v.o) + '</td><td><strong>' + esc(v.n) + '</strong><div class="muted small">' + esc(typeLabel(k.split(':')[0])) + ' #' + esc(k.split(':')[1]) + '</div></td><td>' +
        v.f.filter(f => f[0] !== 'STALE').map(f => '<span class="rc">' + esc(f[0]) + '</span> ' + esc(f[2])).join('<br>') + '</td></tr>').join('') + '</tbody></table></div>' +
      (items.length > 200 ? '<p class="muted small" style="margin-top:6px">Showing 200 of ' + items.length + '. The pre-migration report lists all of them.</p>' : '')
      : '<p class="muted">Nothing predicted to fail in this organization.</p>';
  }
  $('#orgDetail').innerHTML = h;
  if (r) { renderStrip($('#orgStrip'), r.outcomes); renderLegend($('#orgLegend'), r.outcomes); }
  $all('#orgDetail button[data-org]').forEach(b => b.addEventListener('click', () => selectOrg(b.dataset.org, true)));
  $('#orgDetail').scrollIntoView({block: 'nearest'});
}

/* Links */
function linksTab() {
  fillSelect($('#lType'), [...new Set(LINKS.map(l => l.type))].sort(), 'All types', typeLabel);
  if (HAS_RISK) { $('#lRiskField').hidden = false; fillSelect($('#lRisk'), OUTCOMES, 'Any', k => OUTCOME_LABEL[k]); }
  const cols = [
    {key: 'org', label: 'Organization', render: l => '<strong>' + esc(l.org) + '</strong>'},
    {key: 'from', label: 'Borrows from'},
    {key: 'name', label: 'Shared object', render: l => esc(l.name) + '<div class="muted small">' + esc(typeLabel(l.type)) + ' #' + esc(l.id) + '</div>'},
    {key: 'uses', label: 'Used by', cls: 'num', render: l => fmtNum(l.uses)}
  ];
  if (HAS_RISK) cols.push({key: 'outcome', label: 'Predicted', render: l => pill(l.outcome), sort: l => OUTCOMES.indexOf(l.outcome)});
  const t = DataTable($('#linksTable'), cols, {onRow: l => selectOrg(l.org), sortKey: HAS_RISK ? 'outcome' : 'org'});
  const apply = () => {
    const q = $('#lSearch').value.trim().toLowerCase(), ty = $('#lType').value, ro = HAS_RISK ? $('#lRisk').value : '';
    const rows = LINKS.filter(l => (!ty || l.type === ty) && (!ro || l.outcome === ro) && (!q || (l.org + ' ' + l.from + ' ' + l.name).toLowerCase().includes(q)));
    t.set(rows); $('#lCount').textContent = fmtNum(rows.length) + ' links';
  };
  ['lSearch', 'lType', 'lRisk'].forEach(id => $('#' + id).addEventListener('input', apply));
  $('#lCsv').addEventListener('click', () => downloadCSV('cross-org-dependencies.csv', ['Organization', 'Borrows from', 'Type', 'ID', 'Shared object', 'Used by', 'Used by objects', 'Predicted outcome'],
    t.rows().map(l => [l.org, l.from, l.type, l.id, l.name, l.uses, l.used_by.map(u => u.type + ':' + u.id + ' ' + u.name).join('; '), l.outcome ? OUTCOME_LABEL[l.outcome] : ''])));
  apply();
}

function blockersTab() {
  if (!HAS_RISK) return;
  const map = {};
  LINKS.filter(l => ['will_fail', 'at_risk'].includes(l.outcome)).forEach(l => {
    const k = l.type + ':' + l.id;
    const b = map[k] = map[k] || {key: k, type: l.type, id: l.id, name: l.name, owner: l.from, outcome: l.outcome, orgs: new Set(), uses: 0};
    b.orgs.add(l.org); b.uses += l.uses;
  });
  const rows = Object.values(map).map(b => Object.assign(b, {norgs: b.orgs.size, orglist: [...b.orgs].sort().join(', '), why: (risk(b.type, b.id) || {f: []}).f.filter(f => f[0] !== 'STALE').map(f => f[0] + ' ' + f[2]).join('; ')}));
  $('#tabBlockers').hidden = false; $('#cBlockers').textContent = fmtNum(rows.length);
  const t = DataTable($('#blockersTable'), [
    {key: 'outcome', label: 'Predicted', render: b => pill(b.outcome), sort: b => OUTCOMES.indexOf(b.outcome)},
    {key: 'name', label: 'Shared object', render: b => '<strong>' + esc(b.name) + '</strong><div class="muted small">' + esc(typeLabel(b.type)) + ' #' + esc(b.id) + ', owned by ' + esc(b.owner) + '</div>'},
    {key: 'why', label: 'Why', render: b => esc(b.why)},
    {key: 'norgs', label: 'Blocks organizations', render: b => '<strong>' + b.norgs + '</strong> <span class="muted small">' + esc(b.orglist) + '</span>', sort: b => -b.norgs},
    {key: 'uses', label: 'Dependent objects', cls: 'num', render: b => fmtNum(b.uses), sort: b => -b.uses}
  ], {onRow: b => selectOrg(b.owner), sortKey: 'norgs', empty: 'No shared object is predicted to fail.'});
  t.set(rows);
}

initTheme(); initDrawer();
header(); wavesTab(); orgsTab(); linksTab(); blockersTab();
const showTab = initTabs();
"""


def render_dependency_html(view: dict[str, Any], risk: dict[str, Any] | None = None) -> str:
    """Render the Dependency Analyzer UI from a view model (+ optional risk data)."""
    src_version = "2.4"
    if risk:
        src_version = str((risk.get("source") or {}).get("version") or "2.4")
    body = (
        _EXTRA_CSS
        + _BODY.replace("__SOURCE__", escape(view.get("source_url") or "source AAP"))
        .replace("__DATE__", escape(str(view.get("analysis_date", ""))[:16].replace("T", " ")))
        .replace("__SRC_VERSION__", escape(src_version))
    )
    return page("Migration Dependency Map", body, _SCRIPT, {"DEPS": view, "RISK": slim_risk(risk)})


def generate_html_report(
    report: GlobalDependencyReport,
    risk_report: dict[str, Any] | None = None,
) -> str:
    """Generate the self-contained dependency HTML report.

    Args:
        report: Global dependency analysis report
        risk_report: optional dict produced by ``premigration-report`` (JSON)

    Returns:
        Complete HTML string with embedded CSS/JS
    """
    return render_dependency_html(build_view_model(report), risk_report)

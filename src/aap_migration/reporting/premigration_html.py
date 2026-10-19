"""Interactive HTML for the pre-migration (AAP 2.4) readiness report.

v3 — "Pre-migration Assessment" redesign: structured success section,
section descriptions, tooltip on Root-only toggle, cleaner header.
"""

from __future__ import annotations

from html import escape
from typing import Any

from aap_migration.reporting.ui_kit import page

_BODY = """
<header class="masthead">
  <div class="wrap">
    <div class="brand-bar" aria-hidden="true"></div>
    <div class="titles">
      <h1>Pre-migration Assessment</h1>
      <p class="assessment-subtitle">
        Migration AAP __SRC_VERSION__ → 2.6 on OpenShift<span class="sep">·</span>Source AAP __SRC_VERSION__: __SRC_URL__<span class="sep">·</span>Date: __GENERATED__
      </p>
    </div>
    <button class="theme-btn" id="themeBtn" type="button">◐ Theme</button>
  </div>
</header>

<main class="wrap">
  <!-- Success section — structured two-column -->
  <section class="success-section" aria-labelledby="successTitle">
    <div class="success-grid">
      <div class="success-pct-block">
        <div class="pct" id="successPct"></div>
        <div class="pct-sub">first-pass success</div>
      </div>
      <div class="success-detail">
        <p class="summary-line" id="successTitle"></p>
        <div class="outcome-row" id="outcomeRow"></div>
      </div>
    </div>
    <div class="strip" id="strip" role="group" aria-label="Resources by predicted outcome" style="margin-top:20px"></div>
    <div class="legend" id="legend"></div>
  </section>

  <!-- Stat cards -->
  <section class="stat-cards" id="statCards" aria-label="Key numbers" style="margin-bottom:8px"></section>

  <!-- Tabs -->
  <nav class="tabs" role="tablist" aria-label="Report sections">
    <button role="tab" data-tab="overview">Overview</button>
    <button role="tab" data-tab="findings">Findings <span class="count" id="cFindings"></span></button>
    <button role="tab" data-tab="stale">Stale data <span class="count" id="cStale"></span></button>
    <button role="tab" data-tab="orgs">Organizations <span class="count" id="cOrgs"></span></button>
    <button role="tab" data-tab="causes">Root causes</button>
  </nav>

  <!-- Overview -->
  <section class="panel" id="panel-overview" role="tabpanel">
    <div class="grid-2">
      <div class="card stack">
        <div class="section-title"><h2>Failure root causes</h2></div>
        <p class="section-desc">Each bar shows which root-cause rule is responsible for flagging resources. Click a bar to drill into its affected objects.</p>
        <div id="rcBars"></div>
      </div>
      <div class="card stack">
        <div class="section-title"><h2>Readiness by type</h2></div>
        <p class="section-desc">Migration readiness breakdown by AAP resource type. Colour segments show how many objects are ready, at risk, or will fail.</p>
        <div id="typeBars"></div>
      </div>
    </div>
    <div class="card stack" style="margin-top:24px">
      <div class="section-title"><h2>Organizations needing attention</h2></div>
      <p class="section-desc">Organizations with the most objects predicted to fail or be at risk. Click to see specific findings.</p>
      <div id="orgBars"></div>
    </div>
  </section>

  <!-- Findings -->
  <section class="panel" id="panel-findings" role="tabpanel" hidden>
    <p class="section-desc">All individual problems detected across resources. Each row is a finding tied to a root-cause rule. Click a row for full detail and remediation.</p>
    <div class="toolbar">
      <div class="field"><label for="fSearch">Search</label><input type="search" id="fSearch" placeholder="Name, problem or ID…"></div>
      <div class="field"><label for="fImpact">Impact</label><select id="fImpact"></select></div>
      <div class="field"><label for="fRc">Root cause</label><select id="fRc"></select></div>
      <div class="field"><label for="fType">Type</label><select id="fType"></select></div>
      <div class="field"><label for="fOrg">Organization</label><select id="fOrg"></select></div>
      <button class="btn btn-toggle" type="button" id="fCascade" aria-pressed="false">Root only<span class="btn-tooltip">Show only root problems, hide knock-on<br>effects caused by other failing resources</span></button>
      <button class="btn" type="button" id="fReset">Clear</button>
      <button class="btn" type="button" id="fCsv">↓ CSV</button>
      <span class="result-count" id="fCount"></span>
    </div>
    <div id="findingsTable"></div>
  </section>

  <!-- Stale data -->
  <section class="panel" id="panel-stale" role="tabpanel" hidden>
    <p class="section-desc" id="staleIntro"></p>
    <div class="toolbar">
      <div class="field"><label for="sSearch">Search</label><input type="search" id="sSearch" placeholder="Name or owner…"></div>
      <div class="field"><label for="sType">Type</label><select id="sType"></select></div>
      <div class="field"><label for="sOrg">Organization</label><select id="sOrg"></select></div>
      <button class="btn" type="button" id="sCsv">↓ Cleanup CSV</button>
      <span class="result-count" id="sCount"></span>
    </div>
    <div id="staleTable"></div>
  </section>

  <!-- Organizations -->
  <section class="panel" id="panel-orgs" role="tabpanel" hidden>
    <p class="section-desc">All organizations in the AAP estate with object counts and migration readiness breakdown. Click an organization to see its findings.</p>
    <div class="toolbar">
      <div class="field"><label for="oSearch">Search</label><input type="search" id="oSearch" placeholder="Organization name…"></div>
      <span class="result-count" id="oCount"></span>
    </div>
    <div id="orgTable"></div>
  </section>

  <!-- Root causes -->
  <section class="panel" id="panel-causes" role="tabpanel" hidden>
    <p class="section-desc">Catalogue of root-cause rules applied during analysis. Each card explains why objects are flagged and how to fix them before migrating.</p>
    <div id="causes" class="grid-2"></div>
  </section>
</main>
"""

_SCRIPT = r"""
const SEV_TO_OUTCOME = {critical: 'will_fail', high: 'at_risk', medium: 'needs_action', low: 'stale', info: 'ready'};
const S = REPORT.summary;
const FINDINGS = REPORT.findings.map(f => Object.assign(f, {outcome: f.rc === 'STALE' ? 'stale' : SEV_TO_OUTCOME[f.severity]}));
const RES = REPORT.resources;
const byRes = {};
FINDINGS.forEach(f => { (byRes[f.key] = byRes[f.key] || []).push(f); });
let showTab;

/* ── Header / Success section ─────────────────────── */
function header() {
  const total = S.total_resources, out = S.outcomes;
  $('#successPct').textContent = S.predicted_success_pct + '%';
  $('#successTitle').innerHTML = 'Based on analysis of <strong>' + fmtNum(total) + ' objects</strong> across <strong>' + fmtNum(S.organizations) + ' organizations</strong>, ' +
    fmtNum(out.will_fail || 0) + ' will be rejected by AAP 2.6 and ' + fmtNum(out.at_risk || 0) + ' more are at risk.';
  const items = [
    {k: 'will_fail', label: 'Will fail'},
    {k: 'at_risk', label: 'At risk'},
    {k: 'needs_action', label: 'Needs action'},
    {k: 'stale', label: 'Stale'},
    {k: 'ready', label: 'Ready'}
  ];
  $('#outcomeRow').innerHTML = items.map(i =>
    '<div class="outcome-item"><span class="o-dot" style="background:var(--' + i.k + ')"></span>' +
    '<span class="o-val">' + fmtNum(out[i.k] || 0) + '</span><span class="o-lbl">' + i.label + '</span></div>'
  ).join('');
  const pick = o => { if (o === 'ready') return; if (o === 'stale') { showTab('stale', true); return; } setFilters({impact: o}); showTab('findings', true); };
  renderStrip($('#strip'), out, pick);
  renderLegend($('#legend'), out, pick);
  $('#cFindings').textContent = fmtNum(FINDINGS.filter(f => f.rc !== 'STALE').length);
  $('#cStale').textContent = fmtNum(Object.values(RES).filter(r => r.stale).length);
  $('#cOrgs').textContent = fmtNum(Object.keys(REPORT.organizations).length);
}

/* ── Stat cards ───────────────────────────────────── */
function statCards() {
  const out = S.outcomes;
  const cards = [
    {value: fmtNum(S.total_resources), label: 'Total objects', color: ''},
    {value: fmtNum(out.will_fail || 0), label: 'Will fail', color: 'var(--will_fail)'},
    {value: fmtNum(out.at_risk || 0), label: 'At risk', color: 'var(--at_risk)'},
    {value: fmtNum(out.needs_action || 0), label: 'Needs action', color: 'var(--needs_action)'},
    {value: fmtNum(out.stale || 0), label: 'Stale', color: 'var(--stale)'},
    {value: fmtNum(out.ready || 0), label: 'Ready', color: 'var(--ready)'},
  ];
  $('#statCards').innerHTML = cards.map(c =>
    '<div class="stat-card"><div class="stat-value"' + (c.color ? ' style="color:' + c.color + '"' : '') + '>' + c.value + '</div><div class="stat-label">' + c.label + '</div></div>'
  ).join('');
}

/* ── Overview ─────────────────────────────────────── */
function overview() {
  const rcRows = Object.keys(REPORT.rc_catalog).filter(rc => rc !== 'STALE' && S.resources_by_rc[rc]).map(rc => {
    const parts = {}; FINDINGS.filter(f => f.rc === rc).forEach(f => { parts[f.outcome] = (parts[f.outcome] || 0) + 1; });
    return {label: rc + ' — ' + REPORT.rc_catalog[rc].title, html: '<span class="rc">' + rc + '</span> ' + esc(REPORT.rc_catalog[rc].title), parts, total: Object.values(parts).reduce((a, b) => a + b, 0), value: S.resources_by_rc[rc], rc};
  }).sort((a, b) => (b.parts.will_fail || 0) - (a.parts.will_fail || 0) || b.total - a.total);
  renderHBars($('#rcBars'), rcRows, {wrap: true, onClick: r => { setFilters({rc: r.rc}); showTab('findings', true); }});

  const typeRows = Object.keys(S.inventory_counts).map(t => {
    const flagged = S.outcomes_by_type[t] || {}; const parts = Object.assign({}, flagged);
    const n = Object.values(flagged).reduce((a, b) => a + b, 0); parts.ready = (parts.ready || 0) + Math.max(0, S.inventory_counts[t] - n);
    return {label: typeLabel(t), parts, total: S.inventory_counts[t], t};
  }).sort((a, b) => b.total - a.total);
  renderHBars($('#typeBars'), typeRows, {onClick: r => { setFilters({type: r.t}); showTab('findings', true); }});

  const orgRows = Object.entries(REPORT.organizations).map(([name, o]) => ({
    label: name, parts: {will_fail: o.outcomes.will_fail || 0, at_risk: o.outcomes.at_risk || 0},
    total: (o.outcomes.will_fail || 0) + (o.outcomes.at_risk || 0), name
  })).filter(r => r.total).sort((a, b) => b.total - a.total).slice(0, 10);
  renderHBars($('#orgBars'), orgRows, {onClick: r => { setFilters({org: r.name}); showTab('findings', true); }});
}

/* ── Findings tab ─────────────────────────────────── */
let fTable, rootOnly = false;
function setFilters(f) {
  $('#fImpact').value = f.impact || ''; $('#fRc').value = f.rc || ''; $('#fType').value = f.type || ''; $('#fOrg').value = f.org || '';
  $('#fSearch').value = f.q || ''; filterFindings();
}
function filterFindings() {
  const q = $('#fSearch').value.trim().toLowerCase(), imp = $('#fImpact').value, rc = $('#fRc').value, t = $('#fType').value, org = $('#fOrg').value;
  const allFiltered = FINDINGS.filter(f => f.rc !== 'STALE' || rc === 'STALE').filter(f =>
    (!imp || f.outcome === imp) && (!rc || f.rc === rc) && (!t || f.resource_type === t) && (!org || f.organization === org) &&
    (!q || (f.resource_name + ' ' + f.title + ' ' + f.detail + ' ' + f.resource_id).toLowerCase().includes(q)));
  const beforeRoot = allFiltered.length;
  const rows = rootOnly ? allFiltered.filter(f => !f.cascade_from) : allFiltered;
  fTable.set(rows);
  const hidden = beforeRoot - rows.length;
  let countText = fmtNum(rows.length) + ' findings';
  if (rootOnly && hidden > 0) countText += ' (hiding ' + hidden + ' knock-on)';
  $('#fCount').textContent = countText;
}
function findingsTab() {
  fillSelect($('#fImpact'), ['will_fail', 'at_risk', 'needs_action', 'stale'], 'Any impact', o => OUTCOME_LABEL[o]);
  fillSelect($('#fRc'), Object.keys(REPORT.rc_catalog).filter(rc => S.findings_by_rc[rc]), 'All root causes', rc => rc + ' — ' + REPORT.rc_catalog[rc].title);
  fillSelect($('#fType'), [...new Set(FINDINGS.map(f => f.resource_type))].sort(), 'All types', typeLabel);
  fillSelect($('#fOrg'), [...new Set(FINDINGS.map(f => f.organization))].sort(), 'All organizations');
  fTable = DataTable($('#findingsTable'), [
    {key: 'outcome', label: 'Impact', render: f => pill(f.outcome), sort: f => ({critical: 0, high: 1, medium: 2, low: 3, info: 4})[f.severity]},
    {key: 'rc', label: 'Cause', render: f => '<span class="rc" title="' + esc(REPORT.rc_catalog[f.rc].title) + '">' + esc(f.rc) + '</span>'},
    {key: 'resource_name', label: 'Resource', render: f => '<strong>' + esc(f.resource_name) + '</strong><div class="muted small">' + esc(typeLabel(f.resource_type)) + ' #' + esc(f.resource_id) + '</div>'},
    {key: 'organization', label: 'Org'},
    {key: 'title', label: 'Problem', render: f => esc(f.title) + (f.cascade_from ? ' <span class="tag">knock-on</span>' : '')},
    {key: 'modified', label: 'Changed', render: f => { const r = RES[f.key] || {}; return fmtDate(r.modified); }, sort: f => (RES[f.key] || {}).modified || ''}
  ], {onRow: f => openResource(f.key), sortKey: 'outcome'});

  const cascadeBtn = $('#fCascade');
  cascadeBtn.addEventListener('click', function() {
    rootOnly = !rootOnly;
    this.setAttribute('aria-pressed', String(rootOnly));
    filterFindings();
  });

  ['fSearch', 'fImpact', 'fRc', 'fType', 'fOrg'].forEach(id => $('#' + id).addEventListener('input', filterFindings));
  $('#fReset').addEventListener('click', () => {
    rootOnly = false;
    $('#fCascade').setAttribute('aria-pressed', 'false');
    setFilters({});
  });
  $('#fCsv').addEventListener('click', () => downloadCSV('premigration-findings.csv',
    ['Impact', 'Root cause', 'Root cause title', 'Resource type', 'Resource ID', 'Resource', 'Organization', 'Problem', 'Detail', 'Remediation', 'Caused by', 'Last modified', 'Modified by'],
    fTable.rows().map(f => { const r = RES[f.key] || {}; return [OUTCOME_LABEL[f.outcome], f.rc, REPORT.rc_catalog[f.rc].title, f.resource_type, f.resource_id, f.resource_name, f.organization, f.title, f.detail, f.remediation, f.cascade_from || '', r.modified || '', r.modified_by || '']; })));
  filterFindings();
}

/* ── Stale tab ────────────────────────────────────── */
let sTable;
function staleTab() {
  const stale = Object.values(RES).filter(r => r.stale).map(r => Object.assign({}, r, {reason: ((byRes[r.key] || []).find(f => f.rc === 'STALE') || {}).title || ''}));
  $('#staleIntro').textContent = fmtNum(stale.length) + ' objects appear abandoned — never run, failing for months, disabled, or empty. Consider deleting them on AAP 2.4 instead of carrying them into 2.6.';
  fillSelect($('#sType'), [...new Set(stale.map(r => r.resource_type))].sort(), 'All types', typeLabel);
  fillSelect($('#sOrg'), [...new Set(stale.map(r => r.organization))].sort(), 'All organizations');
  sTable = DataTable($('#staleTable'), [
    {key: 'name', label: 'Resource', render: r => '<strong>' + esc(r.name) + '</strong><div class="muted small">' + esc(typeLabel(r.resource_type)) + ' #' + esc(r.id) + '</div>'},
    {key: 'organization', label: 'Org'},
    {key: 'reason', label: 'Reason'},
    {key: 'last_job_run', label: 'Last run', render: r => fmtDate(r.last_job_run)},
    {key: 'modified', label: 'Changed', render: r => fmtDate(r.modified)},
    {key: 'outcome', label: 'If migrated', render: r => pill(r.outcome === 'stale' ? 'ready' : r.outcome)}
  ], {onRow: r => openResource(r.key), sortKey: 'modified'});
  const apply = () => {
    const q = $('#sSearch').value.trim().toLowerCase(), t = $('#sType').value, o = $('#sOrg').value;
    const rows = stale.filter(r => (!t || r.resource_type === t) && (!o || r.organization === o) && (!q || (r.name + ' ' + r.modified_by + ' ' + r.created_by).toLowerCase().includes(q)));
    sTable.set(rows); $('#sCount').textContent = fmtNum(rows.length) + ' stale objects';
  };
  ['sSearch', 'sType', 'sOrg'].forEach(id => $('#' + id).addEventListener('input', apply));
  $('#sCsv').addEventListener('click', () => downloadCSV('premigration-stale-cleanup.csv',
    ['Resource type', 'Resource ID', 'Name', 'Organization', 'Reason', 'Last run', 'Last run failed', 'Last modified', 'Modified by', 'Created by', 'Would also fail import'],
    sTable.rows().map(r => [r.resource_type, r.id, r.name, r.organization, r.reason, r.last_job_run || '', r.last_job_failed ? 'yes' : '', r.modified || '', r.modified_by, r.created_by, r.outcome === 'will_fail' ? 'yes' : ''])));
  apply();
}

/* ── Organizations tab ────────────────────────────── */
function orgsTab() {
  const rows = Object.entries(REPORT.organizations).map(([name, o]) => Object.assign({name}, o, {
    wf: o.outcomes.will_fail || 0, ar: o.outcomes.at_risk || 0, na: o.outcomes.needs_action || 0, st: o.outcomes.stale || 0}));
  const t = DataTable($('#orgTable'), [
    {key: 'name', label: 'Organization', render: r => '<strong>' + esc(r.name) + '</strong>'},
    {key: 'total', label: 'Objects', cls: 'num', render: r => fmtNum(r.total)},
    {key: 'wf', label: 'Will fail', cls: 'num', render: r => r.wf ? '<span style="color:var(--will_fail);font-weight:600">' + fmtNum(r.wf) + '</span>' : '<span class="muted">0</span>'},
    {key: 'ar', label: 'At risk', cls: 'num', render: r => r.ar ? '<span style="color:var(--at_risk)">' + fmtNum(r.ar) + '</span>' : '<span class="muted">0</span>'},
    {key: 'na', label: 'Needs action', cls: 'num', render: r => r.na ? fmtNum(r.na) : '<span class="muted">0</span>'},
    {key: 'st', label: 'Stale', cls: 'num', render: r => r.st ? fmtNum(r.st) : '<span class="muted">0</span>'},
    {key: 'predicted_success_pct', label: 'Success', cls: 'num', render: r => r.predicted_success_pct + '%'},
    {key: 'mix', label: '', sort: false, render: r => '<div class="strip" style="height:6px;min-width:100px">' + OUTCOMES.filter(k => r.outcomes[k]).map(k => '<div class="seg" style="flex:' + r.outcomes[k] + ';background:var(--' + k + ');border-right-width:1px"></div>').join('') + '</div>'}
  ], {onRow: r => { setFilters({org: r.name}); showTab('findings', true); }, sortKey: 'wf', sortDir: -1});
  const apply = () => { const q = $('#oSearch').value.trim().toLowerCase(); const f = rows.filter(r => !q || r.name.toLowerCase().includes(q)); t.set(f); $('#oCount').textContent = fmtNum(f.length) + ' organizations'; };
  $('#oSearch').addEventListener('input', apply); apply();
}

/* ── Root causes tab ──────────────────────────────── */
function causesTab() {
  $('#causes').innerHTML = Object.entries(REPORT.rc_catalog).map(([rc, c]) => {
    const n = S.resources_by_rc[rc] || 0;
    return '<article class="card">' +
      '<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:8px">' +
        '<h3><span class="rc" style="font-size:0.8125rem">' + esc(rc) + '</span> ' + esc(c.title) + '</h3>' +
        '<span class="muted small" style="white-space:nowrap">' + fmtNum(n) + ' objects</span>' +
      '</div>' +
      '<p class="small" style="color:var(--ink-2)">' + esc(c.description) + '</p>' +
      '<p class="small muted" style="margin-top:10px"><strong>Remediation:</strong> ' + esc(c.remediation) + '</p>' +
      (n ? '<p style="margin-top:12px"><button class="btn" type="button" data-rc="' + esc(rc) + '">View affected objects →</button></p>' : '') +
    '</article>';
  }).join('');
  $all('button[data-rc]', $('#causes')).forEach(b => b.addEventListener('click', () => {
    if (b.dataset.rc === 'STALE') { showTab('stale', true); return; } setFilters({rc: b.dataset.rc}); showTab('findings', true); }));
}

/* ── Drawer (resource detail) ─────────────────────── */
function openResource(key) {
  const r = RES[key] || {}; const fs = byRes[key] || [];
  let h = '<p class="muted small" style="margin-bottom:2px">' + esc(typeLabel(r.resource_type)) + ' #' + esc(r.id) + '</p>' +
    '<h2 style="margin-bottom:8px;padding-right:40px">' + esc(r.name) + '</h2>' + pill(r.outcome) +
    '<dl>' +
      '<dt>Organization</dt><dd>' + esc(r.organization) + '</dd>' +
      '<dt>Last changed</dt><dd>' + fmtDate(r.modified) + ' by ' + esc(r.modified_by) + '</dd>' +
      '<dt>Created by</dt><dd>' + esc(r.created_by) + '</dd>' +
      '<dt>Last run</dt><dd>' + fmtDate(r.last_job_run) + (r.last_job_failed ? ' <span class="muted">(failed)</span>' : '') + '</dd>' +
      (r.sync_status ? '<dt>Status</dt><dd>' + esc(r.sync_status) + '</dd>' : '') +
    '</dl>';
  fs.forEach(f => {
    h += '<div class="finding" style="border-left-color:var(--' + f.outcome + ')">' +
      '<p><span class="rc">' + esc(f.rc) + '</span> <strong>' + esc(f.title) + '</strong></p>' +
      '<p class="small" style="color:var(--ink-2)">' + esc(f.detail) + '</p>' +
      '<p class="small muted" style="margin-top:4px"><strong>Fix:</strong> ' + esc(f.remediation) + '</p>' +
      (f.cascade_from ? '<p class="small">Caused by <button class="btn" type="button" data-open="' + esc(f.cascade_from) + '">' + esc(f.cascade_from) + '</button></p>' : '') +
      (f.evidence && Object.keys(f.evidence).length ? '<pre>' + esc(JSON.stringify(f.evidence, null, 2)) + '</pre>' : '') +
    '</div>';
  });
  openDrawer(h);
  $all('button[data-open]', $('#drawerBody')).forEach(b => b.addEventListener('click', () => { if (RES[b.dataset.open]) openResource(b.dataset.open); }));
}

/* ── Init ─────────────────────────────────────────── */
initTheme(); initDrawer();
findingsTab(); staleTab(); orgsTab(); causesTab(); overview(); statCards();
showTab = initTabs(); header();
"""


# Appended only when the report carries AI explanations (C7), so the page is
# byte-identical to the rule-only report when AI is off.
_AI_EXPLANATIONS_SCRIPT = r"""
/* ── AI explanations (optional) ───────────────────── */
(function aiExplanations() {
  const AI = REPORT.ai_explanations || {};
  const cards = $('#causes').children;
  Object.keys(REPORT.rc_catalog).forEach((rc, i) => {
    const e = AI[rc]; const card = cards[i];
    if (!e || !card) return;
    const note = document.createElement('div');
    note.className = 'ai-explanation';
    note.style.cssText = 'margin-top:12px;padding:10px 12px;border-left:3px solid var(--focus);background:var(--line-2)';
    note.innerHTML =
      '<p class="small muted" style="margin-bottom:4px"><strong>AI explanation</strong> · confidence ' + esc(e.confidence) + '</p>' +
      '<p class="small">' + esc(e.summary) + '</p>' +
      '<p class="small" style="color:var(--ink-2);margin-top:6px"><strong>Why it fails:</strong> ' + esc(e.why_it_fails) + '</p>' +
      '<p class="small" style="color:var(--ink-2);margin-top:6px"><strong>What to do:</strong> ' + esc(e.operator_action) + '</p>';
    const button = card.querySelector('button[data-rc]');
    card.insertBefore(note, button ? button.parentNode : null);
  });
})();
"""


def render_premigration_html(report: dict[str, Any]) -> str:
    src = report.get("source", {})
    body = (
        _BODY.replace("__SRC_VERSION__", escape(str(src.get("version") or "2.4")))
        .replace("__SRC_URL__", escape(str(src.get("url") or "source AAP")))
        .replace("__GENERATED__", escape(str(report.get("generated", ""))[:16].replace("T", " ")))
    )
    script = _SCRIPT + _AI_EXPLANATIONS_SCRIPT if report.get("ai_explanations") else _SCRIPT
    return page("Pre-migration Assessment", body, script, {"REPORT": report})

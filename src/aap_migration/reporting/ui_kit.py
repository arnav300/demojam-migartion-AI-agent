"""Shared visual language for the self-contained HTML reports.

Used by the pre-migration report and the Dependency Analyzer UI so both
pages read as one product. Everything is inline (no CDN, no web fonts are
downloaded) because reports are opened in air-gapped environments.

Design notes – v2 (minimal)
----------------------------
* Readiness is expressed with five outcome colours used consistently:
  Will fail, At risk, Needs action, Stale, Ready.
* The hero section shows a single large success percentage and a slim
  readiness strip. Everything else uses generous whitespace, subtle
  borders, and restrained colour to avoid visual overload.
* Cards replace dense tables in overview panels; the detail tables
  remain for drill-down but with softer styling.
"""

from __future__ import annotations

import json
from typing import Any

OUTCOME_COLORS = {
    "will_fail": "#c9190b",
    "at_risk": "#d97a00",
    "needs_action": "#2b6cb0",
    "stale": "#7a6f9b",
    "ready": "#2e7d5b",
}


def embed_json(data: Any) -> str:
    """JSON safe to inline inside a <script> element."""
    return (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


BASE_CSS = """
:root {
  --paper: #f8f9fb;
  --surface: #ffffff;
  --ink: #111827;
  --ink-2: #4b5563;
  --ink-3: #9ca3af;
  --line: #e5e7eb;
  --line-2: #f3f4f6;
  --focus: #2563eb;
  --brand: #ee0000;

  --will_fail: #dc2626;
  --at_risk: #d97706;
  --needs_action: #2563eb;
  --stale: #7c3aed;
  --ready: #059669;

  --will_fail-bg: #fef2f2;
  --at_risk-bg: #fffbeb;
  --needs_action-bg: #eff6ff;
  --stale-bg: #f5f3ff;
  --ready-bg: #ecfdf5;

  --radius: 12px;
  --radius-s: 6px;
  --shadow-sm: 0 1px 2px rgba(0,0,0,.05);
  --shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04);
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #0f1117; --surface: #1a1d27; --ink: #f3f4f6; --ink-2: #d1d5db; --ink-3: #6b7280;
    --line: #374151; --line-2: #1f2937; --focus: #60a5fa;
    --will_fail: #f87171; --at_risk: #fbbf24; --needs_action: #60a5fa; --stale: #a78bfa; --ready: #34d399;
    --will_fail-bg: #2a1515; --at_risk-bg: #2a2210; --needs_action-bg: #15223a; --stale-bg: #1f1a30; --ready-bg: #0f2920;
    --shadow-sm: 0 1px 2px rgba(0,0,0,.2); --shadow: 0 1px 3px rgba(0,0,0,.3);
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --paper: #0f1117; --surface: #1a1d27; --ink: #f3f4f6; --ink-2: #d1d5db; --ink-3: #6b7280;
  --line: #374151; --line-2: #1f2937; --focus: #60a5fa;
  --will_fail: #f87171; --at_risk: #fbbf24; --needs_action: #60a5fa; --stale: #a78bfa; --ready: #34d399;
  --will_fail-bg: #2a1515; --at_risk-bg: #2a2210; --needs_action-bg: #15223a; --stale-bg: #1f1a30; --ready-bg: #0f2920;
  --shadow-sm: 0 1px 2px rgba(0,0,0,.2); --shadow: 0 1px 3px rgba(0,0,0,.3);
  color-scheme: dark;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; }
html { -webkit-text-size-adjust: 100%; }
body {
  background: var(--paper); color: var(--ink);
  font: 14px/1.6 -apple-system, "Segoe UI", system-ui, Roboto, "Helvetica Neue", sans-serif;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}
h1, h2, h3, h4 { line-height: 1.25; }
h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; }
h2 { font-size: 1.125rem; font-weight: 600; }
h3 { font-size: 0.9375rem; font-weight: 600; }
p { margin: 0; }
a { color: var(--focus); text-decoration: none; }
a:hover { text-decoration: underline; }
button, input, select { font: inherit; color: inherit; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.muted { color: var(--ink-3); }
.small { font-size: 0.8125rem; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
@media (max-width: 640px) { .wrap { padding: 0 16px; } }

/* Masthead — clean & flat */
.masthead {
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.masthead .wrap {
  padding-top: 20px; padding-bottom: 16px;
  display: flex; gap: 14px; align-items: center; justify-content: space-between; flex-wrap: wrap;
}
.masthead .brand-bar { width: 4px; align-self: stretch; background: var(--brand); border-radius: 2px; flex: none; }
.masthead .titles { flex: 1; min-width: 200px; }
.masthead h1 { margin-bottom: 2px; }
.masthead .meta { color: var(--ink-3); font-size: 0.8125rem; display: flex; flex-wrap: wrap; gap: 4px 16px; }
.theme-btn {
  border: 1px solid var(--line); background: var(--surface); border-radius: var(--radius-s);
  padding: 5px 10px; cursor: pointer; color: var(--ink-3); font-size: 0.8125rem;
  transition: border-color .15s;
}
.theme-btn:hover { border-color: var(--ink-3); }

/* Hero: success percentage + strip */
.hero { padding: 32px 0 12px; }
.hero-row { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.hero .pct {
  font-size: 3rem; font-weight: 800; letter-spacing: -0.03em; line-height: 1;
  font-variant-numeric: tabular-nums;
}
.hero .pct-label { color: var(--ink-2); font-size: 0.9375rem; max-width: 50ch; }
.strip { display: flex; height: 8px; border-radius: 99px; overflow: hidden; background: var(--line-2); }
.strip .seg { height: 100%; min-width: 2px; border-right: 2px solid var(--paper); cursor: pointer; transition: filter .15s; }
.strip .seg:last-child { border-right: 0; }
.strip .seg:hover { filter: brightness(1.15); }
.legend { display: flex; flex-wrap: wrap; gap: 4px 20px; margin-top: 14px; }
.legend button {
  background: none; border: 0; padding: 2px 0; cursor: pointer;
  display: flex; gap: 6px; align-items: center; font-size: 0.8125rem;
}
.legend .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.legend .n { font-weight: 700; font-size: 0.9375rem; color: var(--ink); }
.legend .l { color: var(--ink-3); }

/* Tabs — underline style, minimal */
.tabs {
  display: flex; gap: 0; border-bottom: 1px solid var(--line); margin-top: 24px; overflow-x: auto;
}
.tabs button {
  background: none; border: 0; border-bottom: 2px solid transparent;
  padding: 10px 16px 8px; cursor: pointer; color: var(--ink-3); font-weight: 500;
  font-size: 0.875rem; white-space: nowrap; transition: color .15s, border-color .15s;
}
.tabs button:hover { color: var(--ink-2); }
.tabs button[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--ink); font-weight: 600; }
.tabs .count { font-weight: 400; color: var(--ink-3); margin-left: 5px; font-size: 0.75rem; }
.panel { padding: 24px 0 48px; }
.panel[hidden] { display: none; }

/* Layout */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }
@media (max-width: 900px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
.section-title { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
.card {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 20px; box-shadow: var(--shadow-sm);
}
.box { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 20px; }
.stack > * + * { margin-top: 20px; }

/* Horizontal bars — slimmer */
.hbars { display: grid; grid-template-columns: minmax(100px, max-content) minmax(0, 1fr) 4em; gap: 6px 12px; align-items: center; }
.hbars .lbl { color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 240px; font-size: 0.8125rem; }
.hbars.wrap-labels { grid-template-columns: minmax(140px, 40%) minmax(0, 1fr) 3.5em; }
.hbars.wrap-labels .lbl { white-space: normal; max-width: none; line-height: 1.35; }
.hbars .lbl button { all: unset; cursor: pointer; }
.hbars .lbl button:hover { text-decoration: underline; }
.hbars .track { height: 6px; background: var(--line-2); border-radius: 99px; display: flex; overflow: hidden; }
.hbars .track span { height: 100%; }
.hbars .val { text-align: right; font-weight: 600; font-size: 0.8125rem; }

/* Controls — lighter touch */
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; margin-bottom: 16px; }
.field { display: flex; flex-direction: column; gap: 3px; }
.field label { font-size: 0.75rem; color: var(--ink-3); text-transform: uppercase; letter-spacing: 0.04em; font-weight: 500; }
.field input, .field select {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-s);
  padding: 6px 10px; min-width: 140px; font-size: 0.8125rem;
  transition: border-color .15s;
}
.field input:focus, .field select:focus { border-color: var(--focus); }
.field input[type="search"] { min-width: 220px; }
.btn {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-s);
  padding: 6px 12px; cursor: pointer; font-weight: 500; font-size: 0.8125rem; color: var(--ink-2);
  transition: all .15s;
}
.btn:hover { border-color: var(--ink-3); color: var(--ink); }
.btn.primary { background: var(--ink); color: var(--surface); border-color: var(--ink); }
.btn[aria-pressed="true"] { background: var(--ink); color: var(--surface); border-color: var(--ink); }
.result-count { margin-left: auto; color: var(--ink-3); font-size: 0.8125rem; align-self: center; }

/* Tables — lighter */
.table-wrap { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); overflow: auto; max-height: 70vh; box-shadow: var(--shadow-sm); }
table.data { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
table.data th {
  position: sticky; top: 0; z-index: 1; background: var(--surface);
  text-align: left; font-weight: 500; color: var(--ink-3); padding: 10px 14px;
  border-bottom: 1px solid var(--line); white-space: nowrap; font-size: 0.75rem;
  text-transform: uppercase; letter-spacing: 0.04em;
}
table.data th.sortable { cursor: pointer; user-select: none; }
table.data th.sortable:hover { color: var(--ink); }
table.data th .arrow { color: var(--ink-3); margin-left: 3px; font-size: 0.65em; }
table.data td { padding: 10px 14px; border-bottom: 1px solid var(--line-2); vertical-align: top; }
table.data tr:last-child td { border-bottom: 0; }
table.data tbody tr.clickable { cursor: pointer; }
table.data tbody tr.clickable:hover td { background: var(--line-2); }
table.data td.num, table.data th.num { text-align: right; }
.trunc { max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { padding: 48px 20px; text-align: center; color: var(--ink-3); font-size: 0.875rem; }
.pager { display: flex; gap: 8px; align-items: center; justify-content: flex-end; margin-top: 10px; color: var(--ink-3); font-size: 0.8125rem; }

/* Pills — compact */
.pill {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 8px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; white-space: nowrap;
}
.pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }
.o-will_fail { color: var(--will_fail); background: var(--will_fail-bg); }
.o-at_risk { color: var(--at_risk); background: var(--at_risk-bg); }
.o-needs_action { color: var(--needs_action); background: var(--needs_action-bg); }
.o-stale { color: var(--stale); background: var(--stale-bg); }
.o-ready { color: var(--ready); background: var(--ready-bg); }
.rc { font-weight: 600; font-size: 0.75rem; color: var(--ink-3); white-space: nowrap; }
.tag {
  display: inline-block; border: 1px solid var(--line); border-radius: var(--radius-s);
  padding: 0 5px; font-size: 0.6875rem; color: var(--ink-3); margin: 0 3px 3px 0;
}

/* Stat cards */
.stat-cards { display: flex; flex-wrap: wrap; gap: 12px; }
.stat-card {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 16px 20px; flex: 1 1 140px; min-width: 120px; box-shadow: var(--shadow-sm);
}
.stat-card .stat-value { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; }
.stat-card .stat-label { font-size: 0.75rem; color: var(--ink-3); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 2px; }

/* Assessment header */
.assessment-subtitle { color: var(--ink-2); font-size: 0.875rem; margin-top: 4px; }
.assessment-subtitle .sep { color: var(--line); margin: 0 6px; }

/* Success section — structured two-column layout */
.success-section { padding: 28px 0 16px; }
.success-grid { display: grid; grid-template-columns: auto 1fr; gap: 0 32px; align-items: center; }
.success-pct-block { text-align: center; padding-right: 32px; border-right: 1px solid var(--line); }
.success-pct-block .pct { font-size: 3.5rem; font-weight: 800; letter-spacing: -0.04em; line-height: 1; color: var(--ink); }
.success-pct-block .pct-sub { font-size: 0.8125rem; color: var(--ink-3); margin-top: 4px; }
.success-detail { display: flex; flex-direction: column; gap: 14px; }
.success-detail .summary-line { font-size: 0.9375rem; color: var(--ink-2); line-height: 1.5; max-width: 56ch; }
.outcome-row { display: flex; flex-wrap: wrap; gap: 8px 24px; }
.outcome-item { display: flex; align-items: center; gap: 6px; font-size: 0.8125rem; }
.outcome-item .o-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.outcome-item .o-val { font-weight: 700; font-size: 0.9375rem; }
.outcome-item .o-lbl { color: var(--ink-3); }

/* Section descriptions */
.section-desc { color: var(--ink-3); font-size: 0.8125rem; margin-bottom: 14px; line-height: 1.45; }

/* Toggle button with tooltip (Root only) */
.btn-toggle { position: relative; }
.btn-toggle[aria-pressed="true"] { background: var(--ink); color: var(--surface); border-color: var(--ink); }
.btn-toggle[aria-pressed="true"]::after { content: " ✓"; }
.btn-toggle .btn-tooltip {
  display: none; position: absolute; bottom: calc(100% + 8px); left: 50%; transform: translateX(-50%);
  background: var(--ink); color: var(--surface); font-size: 0.6875rem; font-weight: 400;
  padding: 5px 10px; border-radius: var(--radius-s); white-space: nowrap; z-index: 10;
  pointer-events: none; box-shadow: var(--shadow); line-height: 1.4;
}
.btn-toggle .btn-tooltip::after {
  content: ""; position: absolute; top: 100%; left: 50%; transform: translateX(-50%);
  border: 5px solid transparent; border-top-color: var(--ink);
}
.btn-toggle:hover .btn-tooltip { display: block; }

/* Drawer — slide-in panel */
.drawer-bg { position: fixed; inset: 0; background: rgba(0,0,0,.25); z-index: 50; backdrop-filter: blur(2px); }
.drawer {
  position: fixed; top: 0; right: 0; bottom: 0; width: min(520px, 100vw);
  background: var(--surface); z-index: 51; border-left: 1px solid var(--line);
  overflow-y: auto; padding: 24px; transform: translateX(100%); transition: transform .2s ease;
  box-shadow: -4px 0 24px rgba(0,0,0,.08);
}
.drawer.open { transform: none; }
.drawer-bg[hidden] { display: none; }
.drawer .close { position: absolute; top: 12px; right: 12px; }
.drawer dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px 14px; margin: 14px 0 0; font-size: 0.8125rem; }
.drawer dt { color: var(--ink-3); font-weight: 500; }
.drawer dd { margin: 0; overflow-wrap: anywhere; }
.drawer .finding { border-left: 3px solid var(--line); padding: 6px 0 6px 14px; margin-top: 16px; }
.drawer .finding p + p { margin-top: 6px; }
.drawer pre { background: var(--paper); border: 1px solid var(--line-2); border-radius: var(--radius-s); padding: 8px 10px; font-size: 0.75rem; overflow-x: auto; margin: 8px 0 0; }
@media (prefers-reduced-motion: reduce) { .drawer, .strip .seg { transition: none; } }
@media print { .tabs, .toolbar, .theme-btn, .pager { display: none !important; } .panel[hidden] { display: block !important; } .table-wrap { max-height: none; } }
"""


BASE_JS = r"""
const OUTCOMES = ['will_fail', 'at_risk', 'needs_action', 'stale', 'ready'];
const OUTCOME_LABEL = {will_fail: 'Will fail', at_risk: 'At risk', needs_action: 'Needs action', stale: 'Stale', ready: 'Ready'};
const TYPE_LABEL = {
  organizations: 'Organizations', users: 'Users', teams: 'Teams', credential_types: 'Credential types',
  credentials: 'Credentials', execution_environments: 'Execution environments', instance_groups: 'Instance groups',
  projects: 'Projects', inventories: 'Inventories', inventory_sources: 'Inventory sources', hosts: 'Hosts',
  inventory_groups: 'Groups', job_templates: 'Job templates', workflow_job_templates: 'Workflow templates',
  schedules: 'Schedules', notification_templates: 'Notifications', credential_input_sources: 'Credential inputs'
};
function typeLabel(t) { return TYPE_LABEL[t] || String(t || '').replace(/_/g, ' '); }
function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function fmtNum(n) { return (n || 0).toLocaleString('en-US'); }
function fmtDate(ts) {
  if (!ts) return '<span class="muted">—</span>';
  const d = new Date(ts); if (isNaN(d)) return esc(ts);
  return d.toISOString().slice(0, 10);
}
function ago(ts) {
  if (!ts) return null; const d = new Date(ts); if (isNaN(d)) return null;
  return Math.round((Date.now() - d.getTime()) / 86400000);
}
function pill(outcome) { return '<span class="pill o-' + outcome + '">' + esc(OUTCOME_LABEL[outcome] || outcome) + '</span>'; }
function $(sel, root) { return (root || document).querySelector(sel); }
function $all(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

function initTheme() {
  try { const t = localStorage.getItem('aapReportTheme'); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
  const btn = $('#themeBtn'); if (!btn) return;
  btn.addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme ||
      (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('aapReportTheme', next); } catch (e) {}
  });
}

function initTabs(onShow) {
  const tabs = $all('.tabs [role="tab"]');
  function show(id, push) {
    tabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.tab === id)));
    $all('.panel').forEach(p => { p.hidden = p.id !== 'panel-' + id; });
    if (push) history.replaceState(null, '', '#' + id);
    if (onShow) onShow(id);
  }
  tabs.forEach(t => t.addEventListener('click', () => show(t.dataset.tab, true)));
  const initial = (location.hash || '').slice(1).split('?')[0];
  show(tabs.some(t => t.dataset.tab === initial) ? initial : tabs[0].dataset.tab, false);
  return show;
}

function renderStrip(el, counts, onPick) {
  const total = OUTCOMES.reduce((a, k) => a + (counts[k] || 0), 0) || 1;
  el.innerHTML = OUTCOMES.filter(k => counts[k]).map(k =>
    '<div class="seg" tabindex="0" role="button" data-o="' + k + '" style="flex:' + counts[k] + ';background:var(--' + k + ')" ' +
    'title="' + OUTCOME_LABEL[k] + ': ' + fmtNum(counts[k]) + ' (' + (100 * counts[k] / total).toFixed(1) + '%)"></div>').join('');
  if (onPick) $all('.seg', el).forEach(s => {
    s.addEventListener('click', () => onPick(s.dataset.o));
    s.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(s.dataset.o); } });
  });
}
function renderLegend(el, counts, onPick) {
  el.innerHTML = OUTCOMES.map(k =>
    '<button type="button" data-o="' + k + '"><span class="dot" style="background:var(--' + k + ')"></span>' +
    '<span class="n">' + fmtNum(counts[k] || 0) + '</span><span class="l">' + OUTCOME_LABEL[k] + '</span></button>').join('');
  if (onPick) $all('button', el).forEach(b => b.addEventListener('click', () => onPick(b.dataset.o)));
}

function renderHBars(el, rows, opts) {
  opts = opts || {};
  const max = Math.max(1, ...rows.map(r => r.total));
  if (!rows.length) { el.innerHTML = '<p class="muted small">No data to display.</p>'; return; }
  el.innerHTML = '<div class="hbars' + (opts.wrap ? ' wrap-labels' : '') + '">' + rows.map((r, i) => {
    const parts = OUTCOMES.filter(k => r.parts[k]).map(k =>
      '<span title="' + OUTCOME_LABEL[k] + ': ' + r.parts[k] + '" style="width:' + (100 * r.parts[k] / max) + '%;background:var(--' + k + ')"></span>').join('');
    const text = r.html || esc(r.label);
    const lbl = opts.onClick ? '<button type="button" data-i="' + i + '">' + text + '</button>' : text;
    return '<div class="lbl" title="' + esc(r.label) + '">' + lbl + '</div><div class="track">' + parts + '</div><div class="val">' + fmtNum(r.value !== undefined ? r.value : r.total) + '</div>';
  }).join('') + '</div>';
  if (opts.onClick) $all('button[data-i]', el).forEach(b => b.addEventListener('click', () => opts.onClick(rows[+b.dataset.i])));
}

function DataTable(container, columns, opts) {
  opts = opts || {};
  const pageSize = opts.pageSize || 100;
  let rows = [], page = 0, sortKey = opts.sortKey || null, sortDir = opts.sortDir || 1;
  container.innerHTML = '<div class="table-wrap"><table class="data"><thead><tr></tr></thead><tbody></tbody></table></div><div class="pager"></div>';
  const thead = $('thead tr', container), tbody = $('tbody', container), pager = $('.pager', container);
  thead.innerHTML = columns.map(c => '<th class="' + (c.sort === false ? '' : 'sortable ') + (c.cls || '') + '" data-k="' + c.key + '" scope="col">' + esc(c.label) + '<span class="arrow"></span></th>').join('');
  $all('th.sortable', thead).forEach(th => th.addEventListener('click', () => {
    const k = th.dataset.k; sortDir = (sortKey === k) ? -sortDir : 1; sortKey = k; apply(); draw();
  }));
  function val(row, k) { const c = columns.find(c => c.key === k); return c && typeof c.sort === 'function' ? c.sort(row) : row[k]; }
  function apply() {
    if (!sortKey) return;
    rows.sort((a, b) => {
      let x = val(a, sortKey), y = val(b, sortKey);
      if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      if (typeof x === 'string') x = x.toLowerCase(); if (typeof y === 'string') y = y.toLowerCase();
      return x < y ? -sortDir : x > y ? sortDir : 0;
    });
  }
  function draw() {
    $all('th', thead).forEach(th => { $('.arrow', th).textContent = th.dataset.k === sortKey ? (sortDir > 0 ? ' ↑' : ' ↓') : ''; });
    const pages = Math.max(1, Math.ceil(rows.length / pageSize)); if (page >= pages) page = pages - 1;
    const slice = rows.slice(page * pageSize, (page + 1) * pageSize);
    tbody.innerHTML = slice.length ? slice.map((r, i) => '<tr class="' + (opts.onRow ? 'clickable' : '') + '" data-i="' + (page * pageSize + i) + '" ' + (opts.onRow ? 'tabindex="0"' : '') + '>' +
      columns.map(c => '<td class="' + (c.cls || '') + '">' + (c.render ? c.render(r) : esc(r[c.key])) + '</td>').join('') + '</tr>').join('')
      : '<tr><td colspan="' + columns.length + '"><div class="empty">' + esc(opts.empty || 'No matching rows. Clear a filter to see more.') + '</div></td></tr>';
    if (opts.onRow) $all('tr.clickable', tbody).forEach(tr => {
      tr.addEventListener('click', () => opts.onRow(rows[+tr.dataset.i]));
      tr.addEventListener('keydown', e => { if (e.key === 'Enter') opts.onRow(rows[+tr.dataset.i]); });
    });
    pager.innerHTML = pages > 1 ? '<button class="btn" data-p="-1"' + (page === 0 ? ' disabled' : '') + '>← Previous</button><span>Page ' + (page + 1) + ' / ' + pages + '</span><button class="btn" data-p="1"' + (page >= pages - 1 ? ' disabled' : '') + '>Next →</button>' : '';
    $all('button[data-p]', pager).forEach(b => b.addEventListener('click', () => { page += +b.dataset.p; draw(); container.scrollIntoView({block: 'start'}); }));
  }
  return { set(newRows) { rows = newRows.slice(); page = 0; apply(); draw(); }, rows() { return rows; } };
}

function openDrawer(html) {
  const d = $('#drawer'), bg = $('#drawerBg');
  $('#drawerBody').innerHTML = html; bg.hidden = false;
  requestAnimationFrame(() => d.classList.add('open'));
  d.setAttribute('aria-hidden', 'false'); $('#drawerClose').focus();
}
function closeDrawer() {
  const d = $('#drawer'); d.classList.remove('open'); d.setAttribute('aria-hidden', 'true'); $('#drawerBg').hidden = true;
}
function initDrawer() {
  $('#drawerClose').addEventListener('click', closeDrawer);
  $('#drawerBg').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
}

function downloadCSV(filename, header, rows) {
  const q = v => { const s = v === null || v === undefined ? '' : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const csv = [header.map(q).join(',')].concat(rows.map(r => r.map(q).join(','))).join('\n');
  const blob = new Blob([csv], {type: 'text/csv;charset=utf-8'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = filename;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
}
function fillSelect(sel, values, allLabel, labelFn) {
  sel.innerHTML = '<option value="">' + esc(allLabel) + '</option>' + values.map(v => '<option value="' + esc(v) + '">' + esc(labelFn ? labelFn(v) : v) + '</option>').join('');
}
"""

DRAWER_HTML = """
<div class="drawer-bg" id="drawerBg" hidden></div>
<aside class="drawer" id="drawer" aria-hidden="true" aria-label="Details">
  <button class="btn close" id="drawerClose" type="button">✕ Close</button>
  <div id="drawerBody"></div>
</aside>
"""


def page(title: str, body: str, script: str, data_scripts: dict[str, Any]) -> str:
    """Assemble a complete self-contained HTML page."""
    data = "\n".join(f"const {name} = {embed_json(value)};" for name, value in data_scripts.items())
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{title}</title>\n<style>{BASE_CSS}</style>\n</head>\n<body>\n"
        f"{body}\n{DRAWER_HTML}\n<script>\n{data}\n{BASE_JS}\n{script}\n</script>\n</body>\n</html>\n"
    )

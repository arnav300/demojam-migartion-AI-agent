"""Tiny stdlib HTTP server: ``/`` serves the page, ``/api/counts`` the JSON."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aap_migration.dashboard.counts import CountsProvider
from aap_migration.utils.logging import get_logger

logger = get_logger(__name__)

POLL_MILLISECONDS = 5000

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Migration live counts</title>
<style>
:root { --paper:#f8f9fb; --surface:#fff; --ink:#111827; --ink-2:#4b5563; --line:#e5e7eb;
        --ok:#059669; --bad:#dc2626; --bar:#2563eb; color-scheme: light; }
@media (prefers-color-scheme: dark) {
  :root { --paper:#0f1117; --surface:#1a1d27; --ink:#f3f4f6; --ink-2:#d1d5db; --line:#2a2e3a;
          --bar:#60a5fa; color-scheme: dark; }
}
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px; background:var(--paper); color:var(--ink);
       font:14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width:1000px; margin:0 auto; }
h1 { font-size:1.4rem; margin:0 0 4px; }
.muted { color:var(--ink-2); font-size:0.85rem; }
.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px; margin:16px 0; }
.card { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.card b { display:block; font-size:1.6rem; font-variant-numeric:tabular-nums; }
.table-wrap { overflow-x:auto; background:var(--surface); border:1px solid var(--line); border-radius:10px; }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
th, td { padding:8px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
th { font-weight:600; color:var(--ink-2); font-size:0.8rem; }
.bar { height:6px; min-width:80px; background:var(--line); border-radius:3px; overflow:hidden; }
.bar > div { height:100%; background:var(--bar); }
.bad { color:var(--bad); }
</style>
</head>
<body>
<main>
  <h1>Migration live counts</h1>
  <p class="muted">Source = AAP 2.4 export · Target = AAP 2.6 (live, read-only) · Migrated = state database.
    Refreshes every 5 s. <span id="status"></span></p>
  <div class="cards" id="cards"></div>
  <div class="table-wrap"><table>
    <thead><tr><th>Resource type</th><th>Source</th><th>Target</th><th>Migrated</th><th>Failed</th><th>Progress</th></tr></thead>
    <tbody id="rows"></tbody>
  </table></div>
</main>
<script>
const POLL_MS = __POLL__;
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => (n === null || n === undefined) ? '—' : Number(n).toLocaleString();
function render(d) {
  const t = d.totals;
  document.getElementById('cards').innerHTML = [
    ['Source objects', t.source], ['Target objects', d.target_connected ? t.target : null],
    ['Migrated', t.migrated], ['Failed', t.failed]
  ].map(([k, v]) => '<div class="card"><span class="muted">' + esc(k) + '</span><b>' + num(v) + '</b></div>').join('');
  document.getElementById('rows').innerHTML = d.resource_types.map(r => {
    const pct = r.source ? Math.min(100, Math.round(100 * r.migrated / r.source)) : 0;
    return '<tr><td>' + esc(r.resource_type) + '</td><td>' + num(r.source) + '</td><td>' + num(r.target) +
      '</td><td>' + num(r.migrated) + '</td><td class="' + (r.failed ? 'bad' : '') + '">' + num(r.failed) +
      '</td><td><div class="bar" title="' + pct + '%"><div style="width:' + pct + '%"></div></div></td></tr>';
  }).join('');
  document.getElementById('status').textContent = 'Updated ' + new Date(d.generated).toLocaleTimeString() + '.';
}
async function poll() {
  try {
    const res = await fetch('api/counts', {cache: 'no-store'});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    render(await res.json());
  } catch (e) {
    document.getElementById('status').textContent = 'Update failed: ' + e.message;
  }
}
poll(); setInterval(poll, POLL_MS);
</script>
</body>
</html>
""".replace("__POLL__", str(POLL_MILLISECONDS))


def make_handler(provider: CountsProvider) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "aap-migrate-dashboard"

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/counts":
                body = json.dumps(provider.get()).encode()
                self._send(HTTPStatus.OK, body, "application/json")
            elif path == "/healthz":
                self._send(HTTPStatus.OK, b"ok", "text/plain")
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            logger.debug("dashboard_request", line=format % args)

    return DashboardHandler


def make_server(host: str, port: int, provider: CountsProvider) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(provider))

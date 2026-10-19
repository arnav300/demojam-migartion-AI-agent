"""Unit tests for the C11 live dashboard."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import pytest
import yaml

from aap_migration.dashboard.counts import (
    CountsProvider,
    TargetCounter,
    source_counts,
    state_counts,
)
from aap_migration.dashboard.server import make_server

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "tests" / "unit" / "ai" / "fixtures" / "failures.db"


@pytest.fixture
def export_dir(tmp_path):
    d = tmp_path / "exports"
    (d / "projects").mkdir(parents=True)
    (d / "projects" / "projects_0001.json").write_text(json.dumps([{"id": 1}, {"id": 2}]))
    (d / "projects" / "projects_0002.json").write_text(json.dumps([{"id": 3}]))
    (d / "projects" / "broken.json").write_text("{not json")
    (d / "credentials").mkdir()
    (d / "credentials" / "c.json").write_text(json.dumps([{"id": 9}]))
    (d / "metadata.json").write_text("{}")  # files at the top level are ignored
    return d


def _target(handler) -> TargetCounter:
    return TargetCounter("https://aap26.example.com/api/controller/v2/", "t0k", transport=httpx.MockTransport(handler))


def test_source_counts(export_dir, tmp_path):
    assert source_counts(export_dir) == {"credentials": 1, "projects": 3}
    assert source_counts(tmp_path / "missing") == {}
    assert source_counts(None) == {}


def test_state_counts_read_only():
    before = DB.read_bytes()
    counts = state_counts(str(DB))
    assert counts["projects"] == {"completed": 1, "failed": 2}
    assert counts["credentials"] == {"failed": 2}
    assert DB.read_bytes() == before
    assert state_counts("/nonexistent/state.db") == {}
    assert state_counts(None) == {}


def test_target_counter_get_only():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.path.endswith("/credentials/"):
            return httpx.Response(200, json={"count": 7, "results": []})
        if request.url.path.endswith("/exports_only/"):
            return httpx.Response(404)
        return httpx.Response(500)

    counts = _target(handler).counts(["credentials", "exports_only", "projects"])

    assert counts == {"credentials": 7, "exports_only": None, "projects": None}
    assert {r.method for r in seen} == {"GET"}
    assert str(seen[0].url) == "https://aap26.example.com/api/controller/v2/credentials/?page_size=1"
    assert seen[0].headers["Authorization"] == "Bearer t0k"


def test_target_counter_timeout_is_none():
    def handler(request):
        raise httpx.ConnectTimeout("down", request=request)

    assert _target(handler).counts(["projects"]) == {"projects": None}


def test_provider_combines_and_caches_for_five_seconds(export_dir):
    calls = []
    now = [100.0]

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"count": 1})

    provider = CountsProvider(export_dir, str(DB), _target(handler), clock=lambda: now[0])
    first = provider.get()
    rows = {r["resource_type"]: r for r in first["resource_types"]}
    assert rows["projects"] == {"resource_type": "projects", "source": 3, "target": 1, "migrated": 1, "failed": 2}
    assert rows["users"]["source"] is None  # only in the state DB
    assert first["totals"]["source"] == 4
    n = len(calls)

    now[0] += 4.9
    assert provider.get() is first
    assert len(calls) == n
    now[0] += 0.2
    assert provider.get() is not first
    assert len(calls) == 2 * n


def test_provider_without_target(export_dir):
    data = CountsProvider(export_dir, None, None).get()
    assert data["target_connected"] is False
    assert all(r["target"] is None for r in data["resource_types"])


def test_server_serves_page_and_counts(export_dir):
    server = make_server("127.0.0.1", 0, CountsProvider(export_dir, str(DB), None))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(f"{base}/").read().decode()
        assert "fetch('api/counts'" in page and "const POLL_MS = 5000;" in page
        counts = json.loads(urllib.request.urlopen(f"{base}/api/counts").read())
        assert {r["resource_type"] for r in counts["resource_types"]} >= {"projects", "credentials"}
        assert urllib.request.urlopen(f"{base}/healthz").read() == b"ok"
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"{base}/nope")
        assert err.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_openshift_dashboard_manifest():
    docs = list(yaml.safe_load_all((ROOT / "deploy" / "openshift" / "dashboard.yaml").read_text()))
    kinds = {d["kind"]: d for d in docs}
    assert set(kinds) == {"Deployment", "Service", "Route"}
    pod = kinds["Deployment"]["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["command"][-4:] == ["--host", "0.0.0.0", "--port", "8080"]
    state_mount = next(m for m in container["volumeMounts"] if m["name"] == "state")
    assert state_mount["readOnly"] is True
    state_volume = next(v for v in pod["volumes"] if v["name"] == "state")
    assert state_volume["persistentVolumeClaim"] == {"claimName": "copilot-state", "readOnly": True}
    assert container["resources"]["requests"] and container["resources"]["limits"]
    assert kinds["Route"]["spec"]["to"]["name"] == kinds["Service"]["metadata"]["name"]

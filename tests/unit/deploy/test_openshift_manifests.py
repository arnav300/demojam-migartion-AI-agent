"""Static checks for the OpenShift kustomize base in deploy/openshift."""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "deploy" / "openshift"
JOBS = ["job-migrate.yaml", "job-premigration.yaml"]
SECRET_KEYS = {
    "AAP_SOURCE_URL",
    "AAP_SOURCE_TOKEN",
    "AAP_TARGET_URL",
    "AAP_TARGET_TOKEN",
    "AI_ENDPOINT",
    "AI_MODEL",
    "AI_TOKEN",
}


def _load(name: str) -> dict:
    return yaml.safe_load((BASE / name).read_text())


def test_configmap_source_matches_repo_config():
    assert (BASE / "config.yaml").read_text() == (ROOT / "config" / "config.yaml").read_text(), (
        "deploy/openshift/config.yaml is out of date: cp config/config.yaml deploy/openshift/"
    )


def test_kustomization_lists_everything_but_example_secret():
    kustomization = _load("kustomization.yaml")
    resources = set(kustomization["resources"])
    assert "secret.example.yaml" not in resources
    expected = {
        p.name
        for p in BASE.glob("*.yaml")
        if p.name not in {"kustomization.yaml", "secret.example.yaml", "config.yaml"}
    }
    assert resources == expected
    assert kustomization["configMapGenerator"][0]["files"] == ["config.yaml"]


def test_example_secret_has_placeholders_only():
    secret = _load("secret.example.yaml")
    assert set(secret["stringData"]) == SECRET_KEYS
    for key, value in secret["stringData"].items():
        assert "<" in value and ">" in value, f"{key} must be a <placeholder>"


def test_real_secret_is_gitignored():
    assert "deploy/openshift/secret.yaml" in (ROOT / ".gitignore").read_text().splitlines()


@pytest.mark.parametrize("name", JOBS)
def test_jobs(name):
    job = _load(name)
    spec = job["spec"]
    assert spec["backoffLimit"] == 0
    pod = spec["template"]["spec"]
    container = pod["containers"][0]

    assert container["resources"]["requests"] and container["resources"]["limits"]
    assert {"name": "state", "mountPath": "/app/state"} in container["volumeMounts"]
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == "copilot-state"
    assert pod["volumes"][1]["configMap"]["name"] == "copilot-config"

    env = {e["name"]: e for e in container["env"]}
    refs = {
        e["valueFrom"]["secretKeyRef"]["key"]: e["valueFrom"]["secretKeyRef"]
        for e in env.values()
        if "valueFrom" in e
    }
    assert set(refs) == SECRET_KEYS
    assert all(ref["name"] == "copilot-secrets" for ref in refs.values())
    # AI must stay optional: the Job runs without the AI_* keys.
    for key in ("AI_ENDPOINT", "AI_MODEL", "AI_TOKEN"):
        assert refs[key].get("optional") is True
    for key in SECRET_KEYS - {"AI_ENDPOINT", "AI_MODEL", "AI_TOKEN"}:
        assert not refs[key].get("optional")
    # No literal secret values in the manifest.
    assert all("value" not in e for e in env.values() if "TOKEN" in e["name"])


def test_buildconfig_passes_branch_zip():
    build = _load("buildconfig.yaml")["spec"]["strategy"]["dockerStrategy"]
    args = {a["name"]: a["value"] for a in build["buildArgs"]}
    assert args["AAP_BRIDGE_ZIP"] == (
        "https://github.com/arnav300/demojam-migartion-AI-agent/archive/refs/heads/"
        "demojam/migration-agent.zip"
    )
    assert build["dockerfilePath"] == "Containerfile"

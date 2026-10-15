"""ISAAC_MANIFEST_FILE=<name> serves data/manifests/<name>.json; unset serves the built-in."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "portal"))
import discovery  # noqa: E402


@pytest.fixture
def manifests(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "MANIFEST_DIR", str(tmp_path))
    return tmp_path


def test_unset_serves_the_builtin(monkeypatch):
    monkeypatch.delenv("ISAAC_MANIFEST_FILE", raising=False)
    assert discovery.get_manifest() == discovery._builtin_manifest()


def test_a_name_serves_that_file(manifests, monkeypatch):
    (manifests / "arm-b.json").write_text(json.dumps({"version": "arm-b"}))
    for name in ("arm-b", "arm-b.json"):
        monkeypatch.setenv("ISAAC_MANIFEST_FILE", name)
        assert discovery.get_manifest() == {"version": "arm-b"}


def test_a_missing_name_raises(manifests, monkeypatch):
    monkeypatch.setenv("ISAAC_MANIFEST_FILE", "nope")
    with pytest.raises(FileNotFoundError):
        discovery.get_manifest()


def test_a_path_is_refused(manifests, monkeypatch):
    monkeypatch.setenv("ISAAC_MANIFEST_FILE", "../secrets")
    with pytest.raises(ValueError):
        discovery.get_manifest()


def test_another_hosts_origin_is_rewritten(manifests, monkeypatch):
    here = discovery._builtin_manifest()["base_path"]
    origin = here.split("/portal")[0]
    (manifests / "x.json").write_text(json.dumps({
        "base_path": "https://elsewhere.example/portal/api",
        "mlflow": "https://elsewhere.example/isaac-mlflow"}))
    monkeypatch.setenv("ISAAC_MANIFEST_FILE", "x")
    m = discovery.get_manifest()
    assert m == {"base_path": here, "mlflow": f"{origin}/isaac-mlflow"}


def test_version_overrides_the_env(manifests, monkeypatch):
    (manifests / "arm-a.json").write_text(json.dumps({"version": "arm-a"}))
    (manifests / "arm-b.json").write_text(json.dumps({"version": "arm-b"}))
    monkeypatch.setenv("ISAAC_MANIFEST_FILE", "arm-a")
    assert discovery.get_manifest("arm-b") == {"version": "arm-b"}
    assert discovery.get_manifest() == {"version": "arm-a"}


def test_builtin_is_selectable_over_the_env(manifests, monkeypatch):
    (manifests / "arm-a.json").write_text(json.dumps({"version": "arm-a"}))
    monkeypatch.setenv("ISAAC_MANIFEST_FILE", "arm-a")
    assert discovery.get_manifest("builtin") == discovery._builtin_manifest()


def test_a_local_json_file_is_served(tmp_path, monkeypatch):
    monkeypatch.delenv("ISAAC_MANIFEST_FILE", raising=False)
    draft = tmp_path / "elsewhere" / "draft.json"
    draft.parent.mkdir()
    draft.write_text(json.dumps({"version": "draft"}))
    assert discovery.get_manifest(str(draft)) == {"version": "draft"}
    monkeypatch.setenv("ISAAC_MANIFEST_FILE", str(draft))
    assert discovery.get_manifest() == {"version": "draft"}


def test_a_path_that_is_not_json_is_refused(manifests):
    with pytest.raises(ValueError):
        discovery.get_manifest("../secrets")

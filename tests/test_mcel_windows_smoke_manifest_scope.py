"""Regression tests for the read-only, bounded Windows manifest smoke probe."""
from __future__ import annotations

import io
import json
from urllib.parse import urlsplit

import pytest

from tools import mcel_migrations_windows_smoke as smoke


def test_default_uses_canonical_bounded_project_and_checks_real_response(monkeypatch, tmp_path):
    seen = []

    def fake_urlopen(req, timeout):
        path = urlsplit(req.full_url).path
        body = json.loads(req.data)
        seen.append((path, body, timeout))
        if path == "/api/applications/task/overview":
            answer = {"ok": True, "processes": [], "server": {}, "hardware": {}}
        elif path == "/api/applications/task/action":
            answer = {"ok": True, "action": "server_status"}
        elif path == "/api/applications/file-explorer/roots":
            answer = {"ok": True, "roots": []}
        elif path == "/api/applications/editor/files":
            answer = {"ok": True, "entries": [{"path": "README.md", "kind": "file"}]}
        elif path == "/api/applications/editor/read":
            answer = {"ok": True, "files": [{"path": "README.md", "content": "test"}]}
        elif path == "/api/applications/editor/project/manifest":
            answer = {"ok": True, "schema": smoke.MANIFEST_SCHEMA,
                      "project_root": smoke.DEFAULT_PROJECT_ROOT,
                      "file_count": 2, "project_manifest_sha256": "f" * 64,
                      "files": [{"path": "application.js", "sha256": "a" * 64, "size": 12},
                                {"path": "mcel.app.json", "sha256": "b" * 64, "size": 24}]}
        else:
            pytest.fail(f"Unexpected smoke route: {path}")
        return io.BytesIO(json.dumps(answer).encode("utf-8"))

    monkeypatch.setattr(smoke.request, "urlopen", fake_urlopen)
    report = tmp_path / "windows-live-smoke.json"
    result = smoke.run(require_windows=False, output=report)
    assert result["overall"] == "PASS_WITH_UNVERIFIED_GATES"
    manifest = result["checks"]["editor.project-manifest"]
    assert manifest["status"] == "PASS"
    assert manifest["evidence"]["file_count"] == 2
    assert manifest["evidence"]["canonical_source_present"] is True
    assert report.exists()
    manifest_requests = [(body, timeout) for path, body, timeout in seen if path.endswith("project/manifest")]
    assert manifest_requests == [({"repo_dir": ".", "project_root": smoke.DEFAULT_PROJECT_ROOT}, 30)]
    assert json.loads(report.read_text(encoding="utf8"))["checks"]["editor.project-manifest"] == manifest


@pytest.mark.parametrize("corruption", [
    {"schema": "other"},
    {"project_root": "."},
    {"file_count": 3},
    {"project_manifest_sha256": "not-a-digest"},
    {"files": []},
])
def test_manifest_contract_rejects_false_positive(corruption):
    valid = {"schema": smoke.MANIFEST_SCHEMA,
             "project_root": smoke.DEFAULT_PROJECT_ROOT, "file_count": 2,
             "project_manifest_sha256": "0" * 64,
             "files": [{"path": "application.js", "size": 1, "sha256": "1" * 64},
                       {"path": "mcel.app.json", "size": 2, "sha256": "2" * 64}]}
    valid.update(corruption)
    with pytest.raises(AssertionError):
        smoke.inspect_manifest_response(valid, smoke.DEFAULT_PROJECT_ROOT)


def test_manifest_check_requires_canonical_app_sources():
    valid = {"schema": smoke.MANIFEST_SCHEMA,
             "project_root": smoke.DEFAULT_PROJECT_ROOT, "file_count": 1,
             "project_manifest_sha256": "0" * 64,
             "files": [{"path": "unrelated.md", "size": 1, "sha256": "1" * 64}]}
    with pytest.raises(AssertionError, match="Canonical Code Editor"):
        smoke.inspect_manifest_response(valid, smoke.DEFAULT_PROJECT_ROOT)

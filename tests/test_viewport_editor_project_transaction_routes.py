from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from main_computer.config import MainComputerConfig
from main_computer.viewport import ViewportServer


ROOT = Path(__file__).resolve().parents[1]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _post(base: str, route: str, body: dict[str, object], *, expected_status: int = 200) -> dict:
    request = Request(
        f"{base}{route}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == expected_status
            return payload
    except HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        assert exc.code == expected_status
        return payload


@pytest.fixture()
def editor_server(tmp_path: Path):
    repo = tmp_path / "main_computer_test"
    project = repo / "apps" / "demo"
    project.mkdir(parents=True)
    (project / "index.html").write_text("<h1>Before</h1>\n", encoding="utf-8")
    (project / "app.js").write_text("console.log('before');\n", encoding="utf-8")
    shutil.copy2(ROOT / "new_patch.py", repo / "new_patch.py")

    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=repo))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", repo, project
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_project_manifest_exposes_transaction_hashes(editor_server) -> None:
    base, _, _ = editor_server
    payload = _post(
        base,
        "/api/applications/editor/project/manifest",
        {"repo_dir": ".", "project_root": "apps/demo"},
    )

    assert payload["ok"] is True
    assert payload["schema"] == "mcel-code-editor-project-manifest-v1"
    assert payload["project_root"] == "apps/demo"
    assert payload["file_count"] == 2
    files = {item["path"]: item for item in payload["files"]}
    assert files["index.html"]["sha256"] == _sha("<h1>Before</h1>\n")
    assert files["app.js"]["sha256"] == _sha("console.log('before');\n")


def test_prepare_and_reviewed_apply_use_server_issued_handle(editor_server) -> None:
    base, _, project = editor_server
    prepared = _post(
        base,
        "/api/applications/editor/project/transaction/prepare",
        {
            "repo_dir": ".",
            "project_root": "apps/demo",
            "changes": [
                {
                    "operation": "modify",
                    "path": "index.html",
                    "expected_before_sha256": _sha("<h1>Before</h1>\n"),
                    "replacement_text": "<h1>After</h1>\n",
                },
                {
                    "operation": "create",
                    "path": "styles/app.css",
                    "replacement_text": "h1 { font-weight: 700; }\n",
                },
            ],
        },
    )

    assert prepared["ok"] is True
    assert prepared["state"] == "prepared"
    assert len(prepared["handle"]) == 32
    assert "path" not in prepared["transaction"]["artifact"]
    assert "report_path" not in prepared["transaction"]
    assert (project / "index.html").read_text(encoding="utf-8") == "<h1>Before</h1>\n"
    assert not (project / "styles" / "app.css").exists()

    blocked = _post(
        base,
        "/api/applications/editor/project/transaction/apply",
        {
            "repo_dir": ".",
            "handle": prepared["handle"],
            "reviewed": True,
            "approved": False,
        },
        expected_status=400,
    )
    assert blocked["ok"] is False

    applied = _post(
        base,
        "/api/applications/editor/project/transaction/apply",
        {
            "repo_dir": ".",
            "handle": prepared["handle"],
            "reviewed": True,
            "approved": True,
        },
    )
    assert applied["ok"] is True
    assert applied["state"] == "applied"
    assert applied["changedFiles"] == [
        "apps/demo/index.html",
        "apps/demo/styles/app.css",
    ]
    assert "receipt_path" not in applied["receipt"]
    assert (project / "index.html").read_text(encoding="utf-8") == "<h1>After</h1>\n"
    assert (project / "styles" / "app.css").read_text(encoding="utf-8") == "h1 { font-weight: 700; }\n"

    invalid = _post(
        base,
        "/api/applications/editor/project/transaction/apply",
        {
            "repo_dir": ".",
            "handle": "../project_edit_transaction.json",
            "reviewed": True,
            "approved": True,
        },
        expected_status=400,
    )
    assert "server-issued transaction handle" in invalid["error"]


def test_explicit_file_save_is_hash_guarded_and_transaction_backed(editor_server) -> None:
    base, _, project = editor_server
    saved = _post(
        base,
        "/api/applications/editor/project/file/save",
        {
            "repo_dir": ".",
            "project_root": "apps/demo",
            "path": "app.js",
            "expected_before_sha256": _sha("console.log('before');\n"),
            "replacement_text": "console.log('after');\n",
            "explicit_save": True,
            "stale_source_checked": True,
            "write_policy": "author-owned-source",
        },
    )

    assert saved["ok"] is True
    assert saved["schema"] == "mcel-code-editor-file-save-v1"
    assert saved["savedPath"] == "apps/demo/app.js"
    assert saved["transaction"]["dry_run"]["ok"] is True
    assert (project / "app.js").read_text(encoding="utf-8") == "console.log('after');\n"

    drifted = _post(
        base,
        "/api/applications/editor/project/file/save",
        {
            "repo_dir": ".",
            "project_root": "apps/demo",
            "path": "app.js",
            "expected_before_sha256": _sha("console.log('before');\n"),
            "replacement_text": "console.log('again');\n",
            "explicit_save": True,
            "stale_source_checked": True,
            "write_policy": "author-owned-source",
        },
        expected_status=400,
    )
    assert drifted["failed_stage"] == "source_verification"
    assert (project / "app.js").read_text(encoding="utf-8") == "console.log('after');\n"


def test_browser_cannot_supply_arbitrary_validation_commands(editor_server) -> None:
    base, _, _ = editor_server
    payload = _post(
        base,
        "/api/applications/editor/project/transaction/prepare",
        {
            "repo_dir": ".",
            "project_root": "apps/demo",
            "changes": [
                {
                    "operation": "modify",
                    "path": "index.html",
                    "expected_before_sha256": _sha("<h1>Before</h1>\n"),
                    "replacement_text": "<h1>After</h1>\n",
                }
            ],
            "validations": [{"argv": ["python", "-c", "print('not allowed')"]}],
        },
        expected_status=400,
    )

    assert payload["ok"] is False
    assert "Browser-supplied validation commands are not allowed" in payload["error"]


def test_browser_cannot_reference_server_side_replacement_file(editor_server, tmp_path: Path) -> None:
    base, _, _ = editor_server
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read\n", encoding="utf-8")
    payload = _post(
        base,
        "/api/applications/editor/project/transaction/prepare",
        {
            "repo_dir": ".",
            "project_root": "apps/demo",
            "changes": [
                {
                    "operation": "modify",
                    "path": "index.html",
                    "expected_before_sha256": _sha("<h1>Before</h1>\n"),
                    "replacement_file": str(secret),
                }
            ],
        },
        expected_status=400,
    )

    assert payload["ok"] is False
    assert "cannot reference server-side replacement files" in payload["error"]

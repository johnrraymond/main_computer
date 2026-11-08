"""Canonical four-app route cutover; no provider calls or consequential effects."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from main_computer.config import MainComputerConfig
# After a rollback the incumbent router intentionally has no promotion resolver.
try:
    from main_computer.viewport_route_dispatch import _semantic_canonical_route
except ImportError:
    pytest.skip("canonical semantic promotion not installed", allow_module_level=True)
from main_computer.viewport_server import ViewportServer


CASES = {
    "calculator": ("semantic-calculator-expanded", "mcel-expanded-ir"),
    "file-explorer": ("semantic-file-manager", "mcel-workbench-ir"),
    "task-manager": ("semantic-task-manager", "mcel-workbench-ir"),
    "code-editor": ("semantic-code-editor", "mcel-workbench-ir"),
}
CANDIDATES = {
    "calculator": "mcel-semantic-calculator-expanded",
    "file-explorer": "mcel-semantic-file-manager",
    "task-manager": "mcel-semantic-task-manager",
    "code-editor": "mcel-semantic-code-editor",
}


def ir_from(page: str, element_id: str) -> dict:
    # Accepted hosts put escaped JSON in one non-executable script element.
    match = re.search(
        rf'<script\b[^>]*\bid="{re.escape(element_id)}"[^>]*>(.*?)</script>',
        page, flags=re.S,
    )
    assert match, f"missing hosted IR: {element_id}"
    return json.loads(match.group(1))


@pytest.fixture(scope="module")
def local_viewport():
    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=Path.cwd()), verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_canonical_resolver_does_not_change_unrelated_routes():
    for prefix in ("applications", "apps", "app"):
        for name in CASES:
            assert _semantic_canonical_route(f"/{prefix}/{name}")[0] == (
                "file-manager" if name == "file-explorer" else name
            )
        assert _semantic_canonical_route(f"/{prefix}/task-manager/connections") == ("task-manager", "connections")
        assert _semantic_canonical_route(f"/{prefix}/task-manager/hardware") == ("task-manager", "cpu-gpu")
        assert _semantic_canonical_route(f"/{prefix}/mcel-lab") is None
        assert _semantic_canonical_route(f"/{prefix}/task-manager/bogus") is None
        assert _semantic_canonical_route(f"/{prefix}/code-editor/other") is None


@pytest.mark.parametrize("name", CASES)
def test_canonical_serves_exactly_the_accepted_candidate_ir(local_viewport, name):
    app_id, element_id = CASES[name]
    with urlopen(f"{local_viewport}/applications/{name}?mcel-shell-child=1", timeout=15) as response:
        page = response.read().decode("utf-8")
        assert response.status == 200
    with urlopen(f"{local_viewport}/applications/{CANDIDATES[name]}", timeout=15) as response:
        candidate = response.read().decode("utf-8")
    promoted_ir = ir_from(page, element_id)
    accepted_ir = ir_from(candidate, element_id)
    assert promoted_ir == accepted_ir
    assert promoted_ir["application"]["appId"] == app_id
    assert "Main Computer Applications" not in page
    assert "Semantic candidate · Original application unchanged" not in page
    if name != "calculator":
        assert "Semantic application ready" in page
        assert "Semantic candidate ready" in candidate


def test_legacy_shell_remains_for_desktop_and_unpromoted_apps(local_viewport):
    for path in ("/applications?mcel-shell-child=1", "/applications/document?mcel-shell-child=1", "/apps/mcel-lab"):
        with urlopen(f"{local_viewport}{path}", timeout=15) as response:
            page = response.read().decode("utf-8")
        assert "Main Computer Applications" in page
        assert "semanticCanonicalApps" in page
        assert "window.location.assign(applicationPath(normalizedApp))" in page


@pytest.mark.parametrize("tab,view", [
    ("server-processes", "server-processes"),
    ("all-processes", "all-processes"),
    ("connections", "connections"),
    ("hardware", "cpu-gpu"),
])
def test_task_manager_existing_bookmarked_views_are_preserved(local_viewport, tab, view):
    with urlopen(f"{local_viewport}/applications/task-manager/{tab}?mcel-shell-child=1", timeout=15) as response:
        page = response.read().decode("utf-8")
    assert f"const initialView={json.dumps(view)};" in page
    assert 'app.execute("intent:switch-view",{choice:initialView})' in page


def test_canonical_routes_retain_local_only_boundary(local_viewport):
    with patch("main_computer.viewport_route_dispatch._hard_halt_client_is_local", return_value=False):
        for name in CASES:
            with pytest.raises(HTTPError) as exc:
                urlopen(f"{local_viewport}/applications/{name}?mcel-shell-child=1", timeout=15)
            assert exc.value.code == 403


def test_unknown_task_tab_is_not_accidentally_promoted(local_viewport):
    with pytest.raises(HTTPError) as exc:
        urlopen(f"{local_viewport}/applications/task-manager/unrecognized", timeout=15)
    assert exc.value.code == 404

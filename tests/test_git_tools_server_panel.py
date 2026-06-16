from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "main_computer" / "web"
WEB_APP = WEB_ROOT / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_git_tools_server_panel_node() -> dict:
    server_panel = SCRIPTS / "git-tools-server-panel.js"
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
const context = {{
  console,
  window: null,
  gitToolsRepoDirValue: (fallback) => fallback || ".",
}};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync({json.dumps(str(server_panel))}, "utf8"), context, {{filename: "git-tools-server-panel.js"}});
const api = context.GitToolsServerPanel;
const noRepo = api.gitServerTargetFromStatus({{ok: false, repo_dir: "repo", error: "not a repo"}});
const localRemote = api.gitServerTargetFromStatus({{
  ok: true,
  repo_dir: "repo",
  is_git_repo: true,
  has_head: true,
  git_root: "C:/work/My Repo",
  remotes: [{{name: "local-gitea", fetch: "http://localhost:3000/local/my-repo.git"}}]
}});
const legacySsh = api.gitServerTargetFromStatus({{
  ok: true,
  repo_dir: "repo",
  is_git_repo: true,
  has_head: true,
  git_root: "C:/work/Legacy Repo",
  remotes: [{{name: "local-gitea", fetch: "ssh://git@localhost:2222/local/legacy-repo.git"}}]
}});
console.log(JSON.stringify({{
  sourceFile: api.sourceFile,
  surfaceId: api.surfaceId,
  version: api.version,
  exportNames: Object.keys(api).sort(),
  compatRefresh: context.refreshGitServerStatus === api.refreshGitServerStatus,
  compatRender: context.renderGitServerStatus === api.renderGitServerStatus,
  cleaned: api.gitServerCleanRemoteSegment("hello world"),
  baseName: api.gitServerBaseName("C:/work/My Repo/"),
  localUrl: api.gitServerLocalGiteaUrl("local owner", "repo name"),
  parsedHttp: api.gitServerParseLocalGiteaUrl("http://localhost:3000/local/repo.git"),
  parsedSsh: api.gitServerParseLocalGiteaUrl("ssh://git@localhost:2222/local/repo.git"),
  dockerWarning: api.gitServerDockerUnavailableText({{compose_file: "docker-compose.dev.yml"}}),
  noRepo,
  localRemote,
  legacySsh,
}}));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_git_tools_server_panel_loads_between_status_api_and_legacy_bridge() -> None:
    html = (WEB_ROOT / "applications.html").read_text(encoding="utf-8")
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    git_tools = (SCRIPTS / "git-tools.js").read_text(encoding="utf-8")
    server_panel = (SCRIPTS / "git-tools-server-panel.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/git-tools-server-panel.js -->" in html
    assert html.index("git-tools-status-api.js") < html.index("git-tools-server-panel.js")
    assert html.index("git-tools-server-panel.js") < html.index("task-manager.js")
    assert html.index("git-tools-server-panel.js") < html.index("git-tools.js")

    assert "global.GitToolsServerPanel" in server_panel
    assert "git-tools.server-panel" in server_panel
    assert "function renderGitServerStatus" in server_panel
    assert "async function refreshGitServerStatus" in server_panel
    assert "async function runGitServerAction" in server_panel
    assert "async function runGitServerOperationRequest" in server_panel

    assert "function renderGitServerStatus" not in task_manager
    assert "async function refreshGitServerStatus" not in task_manager
    assert "async function runGitServerAction" not in task_manager
    assert "async function runGitServerOperationRequest" not in task_manager

    assert "GitToolsServerPanel" in git_tools
    assert "serverPanel" in git_tools


def test_git_tools_server_panel_exports_compatibility_surface_and_helpers() -> None:
    report = _run_git_tools_server_panel_node()

    assert report["sourceFile"].endswith("git-tools-server-panel.js")
    assert report["surfaceId"] == "git-tools.server-panel"
    assert report["version"] == "0.1.0"
    assert report["compatRefresh"]
    assert report["compatRender"]

    for export_name in [
        "renderGitServerStatus",
        "refreshGitServerStatus",
        "runGitServerAction",
        "refreshGitServerTargetPrefunk",
        "gitServerTargetFromStatus",
        "useLocalGitServerRemote",
        "applyLocalGitServerRemote",
        "pushLocalGitServerRemote",
    ]:
        assert export_name in report["exportNames"]

    assert report["cleaned"] == "hello-world"
    assert report["baseName"] == "My-Repo"
    assert report["localUrl"] == "http://localhost:3000/local-owner/repo-name.git"
    assert report["parsedHttp"] == {
        "protocol": "http",
        "owner": "local",
        "repo": "repo",
        "url": "http://localhost:3000/local/repo.git",
    }
    assert report["parsedSsh"] == {
        "protocol": "ssh",
        "owner": "local",
        "repo": "repo",
        "url": "ssh://git@localhost:2222/local/repo.git",
    }
    assert "Docker CLI is not available" in report["dockerWarning"]

    assert report["noRepo"]["is_git_repo"] is False
    assert report["noRepo"]["target"]["remote"] == "local-gitea"
    assert report["localRemote"]["target"]["source"] == "detected-from-git-remote"
    assert report["localRemote"]["target"]["url"] == "http://localhost:3000/local/my-repo.git"
    assert report["legacySsh"]["target"]["source"] == "detected-legacy-ssh-local-gitea-remote"
    assert report["legacySsh"]["target"]["url"] == "http://localhost:3000/local/legacy-repo.git"

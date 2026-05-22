from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.sqlite_publish import (
    SQLitePublishError,
    configure_sqlite_database_resource,
    ensure_sqlite_publish_smoke_source,
    publish_site_sqlite_databases,
)
from main_computer.website_project_manifest import create_website_project, publish_website


def _blog_title(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT title FROM blog_posts WHERE id = 'post_001'").fetchone()
    assert row is not None
    return str(row[0])


def _make_unknown_remote_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE remote_only_posts (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
        conn.execute("INSERT INTO remote_only_posts (id, title) VALUES ('live_001', 'Do not overwrite me')")
        conn.commit()


def test_sqlite_publish_smoke_creates_readable_artifact_manifest_and_safe_second_pass(tmp_path: Path) -> None:
    create_website_project(tmp_path, "cms-site", "CMS Site")
    configure_sqlite_database_resource(tmp_path, "cms-site")
    ensure_sqlite_publish_smoke_source(tmp_path, "cms-site")

    first = publish_site_sqlite_databases(tmp_path, "cms-site", lane="local")
    assert len(first) == 1
    assert first[0]["ok"] is True
    assert first[0]["action"] == "created"
    assert first[0]["verified"] is True

    artifact = tmp_path / "runtime" / "websites" / "cms-site" / "dist" / "data" / "content.sqlite"
    manifest_path = artifact.parent / "publish-manifest.json"
    source = tmp_path / "runtime" / "websites" / "cms-site" / "data" / "content.sqlite"

    assert artifact.is_file()
    assert manifest_path.is_file()
    assert artifact.resolve() != source.resolve()
    assert _blog_title(artifact) == "Hello Blog"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["database"]["connection"] == "content"
    assert manifest["database"]["adapter"] == "sqlite"
    assert manifest["database"]["artifact"] == "data/content.sqlite"
    assert manifest["database"]["protect_existing_deployed_database"] is True
    assert manifest["resources"][0]["id"] == "blog_posts:post_001"
    assert manifest["resources"][0]["slug"] == "hello-blog"

    second = publish_site_sqlite_databases(tmp_path, "cms-site", lane="local")
    assert second[0]["action"] == "unchanged"
    assert second[0]["logical_changes"] is False
    assert second[0]["changed_resources"] == []

    with sqlite3.connect(source) as conn:
        conn.execute(
            """
            UPDATE blog_posts
            SET title = 'Hello Blog Updated',
                updated_at = '2026-01-02T00:00:00.000Z'
            WHERE id = 'post_001'
            """
        )
        conn.commit()

    third = publish_site_sqlite_databases(tmp_path, "cms-site", lane="local")
    assert third[0]["action"] == "staged"
    assert third[0]["logical_changes"] is True
    assert third[0]["changed_resources"] == ["blog_posts:post_001"]

    staged_artifact = tmp_path / third[0]["staged_artifact"]
    staged_manifest = tmp_path / third[0]["staged_manifest"]
    assert staged_artifact.is_file()
    assert staged_manifest.is_file()
    assert _blog_title(staged_artifact) == "Hello Blog Updated"

    # The live published DB remains the old version until an explicit promotion path exists.
    assert _blog_title(artifact) == "Hello Blog"


def test_sqlite_deploy_path_refuses_unknown_existing_db_without_overwrite(tmp_path: Path) -> None:
    create_website_project(tmp_path, "cms-site", "CMS Site")
    configure_sqlite_database_resource(tmp_path, "cms-site")
    ensure_sqlite_publish_smoke_source(tmp_path, "cms-site")

    remote_root = tmp_path / "remote-deploy-target"
    remote_db = remote_root / "data" / "content.sqlite"
    _make_unknown_remote_db(remote_db)

    with pytest.raises(SQLitePublishError, match="Refusing to overwrite existing SQLite artifact"):
        publish_site_sqlite_databases(tmp_path, "cms-site", lane="deploy", output_root=remote_root)

    with sqlite3.connect(remote_db) as conn:
        row = conn.execute("SELECT title FROM remote_only_posts WHERE id = 'live_001'").fetchone()
    assert row == ("Do not overwrite me",)


def test_website_publish_runs_sqlite_publish_before_local_server_deploy_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_website_project(tmp_path, "hub-site", "Hub Site")
    configure_sqlite_database_resource(tmp_path, "hub-site")
    ensure_sqlite_publish_smoke_source(tmp_path, "hub-site")
    (tmp_path / "deploy" / "local-platform").mkdir(parents=True)
    (tmp_path / "deploy" / "local-platform" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="started\n", stderr="")

    monkeypatch.setattr("main_computer.website_project_manifest.subprocess.run", fake_run)

    result = publish_website(tmp_path, "hub-site", lane="local", verify=False)

    assert result["ok"] is True
    assert result["database_publish"][0]["action"] == "created"
    assert result["database_publish"][0]["verified"] is True
    assert calls
    assert calls[0][-1] == "hub-local"

    artifact = tmp_path / "runtime" / "websites" / "hub-site" / "dist" / "data" / "content.sqlite"
    assert artifact.is_file()
    assert _blog_title(artifact) == "Hello Blog"

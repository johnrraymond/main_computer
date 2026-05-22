from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from main_computer.website_project_manifest import (
    WebsiteProject,
    WebsiteProjectError,
    load_website_project,
    utc_now,
    write_json,
)


BLOG_POSTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS blog_posts (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  content TEXT NOT NULL,
  status TEXT NOT NULL,
  published_at TEXT,
  updated_at TEXT NOT NULL
);
"""

PUBLISH_RESOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS _publish_resources (
  resource_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  source_table TEXT,
  source_id TEXT,
  hash TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

SMOKE_SEED_POST = {
    "id": "post_001",
    "title": "Hello Blog",
    "slug": "hello-blog",
    "content": "This is the first smoke-test post.",
    "status": "published",
    "published_at": "2026-01-01T00:00:00.000Z",
    "updated_at": "2026-01-01T00:00:00.000Z",
}


class SQLitePublishError(WebsiteProjectError):
    """Raised when a configured SQLite database cannot be safely published."""


@dataclass(frozen=True)
class SQLiteDatabaseConnection:
    name: str
    adapter: str
    source_path: str
    artifact: str
    publishable: bool
    protect_existing_deployed_database: bool
    existing_remote_behavior: str

    def to_manifest_config(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "path": self.source_path,
            "artifact": self.artifact,
            "publishable": self.publishable,
            "protect_existing_deployed_database": self.protect_existing_deployed_database,
            "existing_remote_behavior": self.existing_remote_behavior,
        }


def default_sqlite_connection_config(
    *,
    path: str = "./data/content.sqlite",
    artifact: str = "data/content.sqlite",
    publishable: bool = True,
    protect_existing_deployed_database: bool = True,
    existing_remote_behavior: str = "refuse_unknown",
) -> dict[str, Any]:
    return {
        "adapter": "sqlite",
        "path": path,
        "artifact": artifact,
        "publishable": bool(publishable),
        "protect_existing_deployed_database": bool(protect_existing_deployed_database),
        "existing_remote_behavior": existing_remote_behavior,
    }


def configure_sqlite_database_resource(
    repo_root: Path,
    site_id: object,
    *,
    connection: str = "content",
    path: str = "./data/content.sqlite",
    artifact: str = "data/content.sqlite",
    publishable: bool = True,
    protect_existing_deployed_database: bool = True,
    existing_remote_behavior: str = "refuse_unknown",
) -> WebsiteProject:
    """Store the backend-page SQLite publish settings in the website manifest."""

    project = load_website_project(repo_root, site_id)
    manifest = dict(project.manifest)
    backend = manifest.setdefault("backend", {})
    if not isinstance(backend, dict):
        backend = {}
        manifest["backend"] = backend
    databases = backend.setdefault("databases", {})
    if not isinstance(databases, dict):
        databases = {}
        backend["databases"] = databases
    connections = databases.setdefault("connections", {})
    if not isinstance(connections, dict):
        connections = {}
        databases["connections"] = connections
    clean_connection = _validate_connection_name(connection)
    connections[clean_connection] = default_sqlite_connection_config(
        path=path,
        artifact=artifact,
        publishable=publishable,
        protect_existing_deployed_database=protect_existing_deployed_database,
        existing_remote_behavior=existing_remote_behavior,
    )
    manifest["updated_at"] = utc_now()
    write_json(project.path / "site.json", manifest)
    return load_website_project(repo_root, project.id)


def sqlite_database_connections(project: WebsiteProject) -> list[SQLiteDatabaseConnection]:
    backend = project.manifest.get("backend")
    if not isinstance(backend, dict):
        return []
    databases = backend.get("databases")
    if not isinstance(databases, dict):
        return []
    connections = databases.get("connections")
    if not isinstance(connections, dict):
        return []

    parsed: list[SQLiteDatabaseConnection] = []
    for name, raw_config in sorted(connections.items()):
        if not isinstance(raw_config, dict):
            continue
        adapter = str(raw_config.get("adapter") or "").strip().lower()
        if adapter != "sqlite":
            continue
        publishable = bool(raw_config.get("publishable"))
        source_path = str(raw_config.get("path") or "./data/content.sqlite").strip()
        artifact = str(raw_config.get("artifact") or "data/content.sqlite").strip()
        parsed.append(
            SQLiteDatabaseConnection(
                name=_validate_connection_name(name),
                adapter=adapter,
                source_path=source_path,
                artifact=_validate_artifact_path(artifact),
                publishable=publishable,
                protect_existing_deployed_database=bool(raw_config.get("protect_existing_deployed_database", True)),
                existing_remote_behavior=str(raw_config.get("existing_remote_behavior") or "refuse_unknown").strip() or "refuse_unknown",
            )
        )
    return parsed


def ensure_sqlite_publish_smoke_source(
    repo_root: Path,
    site_id: object,
    *,
    connection: str = "content",
) -> dict[str, Any]:
    """Create the configured SQLite source DB, schema, and seed record without clobbering existing rows."""

    project = load_website_project(repo_root, site_id)
    db = _connection_by_name(project, connection)
    if not db.publishable:
        raise SQLitePublishError(f"SQLite database connection {db.name!r} is not publishable.")
    source_path = resolve_source_db_path(project, db)
    source_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(source_path) as conn:
        conn.execute(BLOG_POSTS_SCHEMA)
        conn.execute(PUBLISH_RESOURCES_SCHEMA)
        conn.execute(
            """
            INSERT OR IGNORE INTO blog_posts
              (id, title, slug, content, status, published_at, updated_at)
            VALUES
              (:id, :title, :slug, :content, :status, :published_at, :updated_at)
            """,
            SMOKE_SEED_POST,
        )
        resources = _database_resources(conn)
        conn.executemany(
            """
            INSERT INTO _publish_resources
              (resource_id, kind, source_table, source_id, hash, updated_at)
            VALUES
              (:id, :kind, :table, :recordId, :hash, :updatedAt)
            ON CONFLICT(resource_id) DO UPDATE SET
              kind=excluded.kind,
              source_table=excluded.source_table,
              source_id=excluded.source_id,
              hash=excluded.hash,
              updated_at=excluded.updated_at
            """,
            [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "table": item["table"],
                    "recordId": item["recordId"],
                    "hash": item["hash"],
                    "updatedAt": item.get("updatedAt") or utc_now(),
                }
                for item in resources
            ],
        )
        conn.commit()

    return {
        "ok": True,
        "site_id": project.id,
        "connection": db.name,
        "source": _repo_relative_or_abs(repo_root, source_path),
        "resource_count": len(resources),
        "resources": resources,
    }


def publish_site_sqlite_databases(
    repo_root: Path,
    site_id: object,
    *,
    lane: object = "local",
    output_root: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Publish each configured publishable SQLite database for a site.

    The default behavior is protective: an existing target DB without a matching
    publish manifest is refused, and changed owned DBs are staged instead of
    destructively replacing the current deployed artifact.
    """

    project = load_website_project(repo_root, site_id)
    results: list[dict[str, Any]] = []
    for db in sqlite_database_connections(project):
        if not db.publishable:
            continue
        results.append(
            publish_sqlite_database(
                repo_root,
                project,
                db,
                lane=lane,
                output_root=output_root,
                dry_run=dry_run,
                force=force,
            )
        )
    return results


def publish_sqlite_database(
    repo_root: Path,
    project: WebsiteProject,
    db: SQLiteDatabaseConnection,
    *,
    lane: object = "local",
    output_root: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    source_path = resolve_source_db_path(project, db)
    if not source_path.is_file():
        if dry_run:
            output_base = output_root if output_root is not None else default_sqlite_publish_output_root(project, lane)
            artifact_rel = PurePosixPath(db.artifact)
            artifact_path = (Path(output_base) / Path(*artifact_rel.parts)).resolve()
            manifest_path = artifact_path.parent / "publish-manifest.json"
            return {
                "ok": True,
                "dry_run": True,
                "site_id": project.id,
                "connection": db.name,
                "lane": str(lane or "local"),
                "source": _repo_relative_or_abs(repo_root, source_path),
                "source_exists": False,
                "artifact": _repo_relative_or_abs(repo_root, artifact_path),
                "manifest": _repo_relative_or_abs(repo_root, manifest_path),
                "would_error": f"Configured SQLite source DB does not exist: {source_path}",
                "resource_count": 0,
            }
        raise SQLitePublishError(f"Configured SQLite source DB does not exist: {source_path}")

    output_base = output_root if output_root is not None else default_sqlite_publish_output_root(project, lane)
    output_base = Path(output_base)
    artifact_rel = PurePosixPath(db.artifact)
    artifact_path = (output_base / Path(*artifact_rel.parts)).resolve()
    manifest_path = artifact_path.parent / "publish-manifest.json"
    staged_artifact_path = artifact_path.with_name(artifact_path.name + ".staged")
    staged_manifest_path = manifest_path.with_name("publish-manifest.staged.json")

    try:
        artifact_path.relative_to(output_base.resolve())
    except ValueError as exc:
        raise SQLitePublishError("SQLite publish artifact escaped the output root.") from exc
    if source_path.resolve() == artifact_path:
        raise SQLitePublishError("SQLite publish artifact path must not point at the source authoring DB.")

    resources = sqlite_publish_resources(source_path)
    current_manifest = build_publish_manifest(
        repo_root,
        project,
        db,
        source_path=source_path,
        artifact_sha256=sha256_file(source_path),
        resources=resources,
        target_lane=str(lane or "local"),
    )

    existing_manifest = _read_json_object(manifest_path)
    existing_artifact = artifact_path.exists()
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "site_id": project.id,
            "connection": db.name,
            "lane": str(lane or "local"),
            "source": _repo_relative_or_abs(repo_root, source_path),
            "artifact": _repo_relative_or_abs(repo_root, artifact_path),
            "manifest": _repo_relative_or_abs(repo_root, manifest_path),
            "would_block_unknown_existing_db": bool(existing_artifact and existing_manifest is None and db.protect_existing_deployed_database and not force),
            "resource_count": len(resources),
        }

    if existing_artifact and existing_manifest is None and db.protect_existing_deployed_database and not force:
        raise SQLitePublishError(
            f"Refusing to overwrite existing SQLite artifact without a matching publish manifest: {artifact_path}"
        )

    if existing_artifact and existing_manifest is not None and not _manifest_owns_database(existing_manifest, project, db):
        if db.protect_existing_deployed_database and not force:
            raise SQLitePublishError(
                f"Refusing to overwrite SQLite artifact owned by a different project/connection: {artifact_path}"
            )

    changed_resources = _changed_resources(existing_manifest, current_manifest) if existing_manifest else [
        resource["id"] for resource in current_manifest["resources"]
    ]
    logical_changes = bool(changed_resources)

    if existing_artifact and existing_manifest is not None and not logical_changes:
        return {
            "ok": True,
            "dry_run": False,
            "site_id": project.id,
            "connection": db.name,
            "lane": str(lane or "local"),
            "action": "unchanged",
            "logical_changes": False,
            "changed_resources": [],
            "source": _repo_relative_or_abs(repo_root, source_path),
            "artifact": _repo_relative_or_abs(repo_root, artifact_path),
            "manifest": _repo_relative_or_abs(repo_root, manifest_path),
            "resource_count": len(resources),
            "message": "No logical DB-backed resources changed; existing deployed SQLite artifact was left in place.",
        }

    if existing_artifact and db.protect_existing_deployed_database and not force:
        staged_artifact_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, staged_artifact_path)
        staged_manifest = dict(current_manifest)
        staged_manifest["database"] = dict(current_manifest["database"])
        staged_manifest["database"]["artifact"] = _posix_relative_to(staged_artifact_path, output_base)
        staged_manifest["staged_for"] = db.artifact
        staged_manifest["artifact_sha256"] = sha256_file(staged_artifact_path)
        write_json(staged_manifest_path, staged_manifest)
        return {
            "ok": True,
            "dry_run": False,
            "site_id": project.id,
            "connection": db.name,
            "lane": str(lane or "local"),
            "action": "staged",
            "logical_changes": True,
            "changed_resources": changed_resources,
            "source": _repo_relative_or_abs(repo_root, source_path),
            "artifact": _repo_relative_or_abs(repo_root, artifact_path),
            "manifest": _repo_relative_or_abs(repo_root, manifest_path),
            "staged_artifact": _repo_relative_or_abs(repo_root, staged_artifact_path),
            "staged_manifest": _repo_relative_or_abs(repo_root, staged_manifest_path),
            "resource_count": len(resources),
            "message": "Existing deployed SQLite artifact was not overwritten; new DB was staged for explicit promotion.",
        }

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, artifact_path)
    final_manifest = dict(current_manifest)
    final_manifest["artifact_sha256"] = sha256_file(artifact_path)
    write_json(manifest_path, final_manifest)
    verified = verify_published_sqlite_database(artifact_path, expected_resources=resources)
    return {
        "ok": True,
        "dry_run": False,
        "site_id": project.id,
        "connection": db.name,
        "lane": str(lane or "local"),
        "action": "created" if not existing_artifact else "replaced",
        "logical_changes": logical_changes,
        "changed_resources": changed_resources,
        "source": _repo_relative_or_abs(repo_root, source_path),
        "artifact": _repo_relative_or_abs(repo_root, artifact_path),
        "manifest": _repo_relative_or_abs(repo_root, manifest_path),
        "resource_count": len(resources),
        "verified": verified,
    }


def default_sqlite_publish_output_root(project: WebsiteProject, lane: object = "local") -> Path:
    """Return the production-shaped local publish folder used by Local Server/Deploy smoke tests."""

    # The artifact path remains lane-independent on purpose: Local Server and
    # Deploy should exercise the same production-shaped DB output rather than a
    # source authoring DB path.
    return project.path / "dist"


def resolve_source_db_path(project: WebsiteProject, db: SQLiteDatabaseConnection) -> Path:
    raw = db.source_path.replace("\\", "/")
    if not raw:
        raise SQLitePublishError("SQLite source DB path is empty.")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise SQLitePublishError("SQLite source DB path must be site-relative.")
    parts = PurePosixPath(raw).parts
    if any(part == ".." for part in parts):
        raise SQLitePublishError("SQLite source DB path may not contain '..'.")
    site_root = project.path.resolve()
    resolved = (site_root / Path(*[part for part in parts if part not in {"."}])).resolve()
    try:
        resolved.relative_to(site_root)
    except ValueError as exc:
        raise SQLitePublishError("SQLite source DB path escaped the website directory.") from exc
    return resolved


def sqlite_publish_resources(source_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(source_path) as conn:
        return _database_resources(conn)


def verify_published_sqlite_database(path: Path, *, expected_resources: list[dict[str, Any]]) -> bool:
    if not path.is_file():
        return False
    with sqlite3.connect(path) as conn:
        conn.execute("SELECT 1 FROM blog_posts LIMIT 1").fetchall()
        actual = _database_resources(conn)
    return {item["id"]: item["hash"] for item in actual} == {
        item["id"]: item["hash"] for item in expected_resources
    }


def build_publish_manifest(
    repo_root: Path,
    project: WebsiteProject,
    db: SQLiteDatabaseConnection,
    *,
    source_path: Path,
    artifact_sha256: str,
    resources: list[dict[str, Any]],
    target_lane: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "generated_at": utc_now(),
        "site": {
            "id": project.id,
            "name": project.name,
        },
        "target": {
            "lane": target_lane,
        },
        "database": {
            "connection": db.name,
            "adapter": db.adapter,
            "source": _repo_relative_or_abs(repo_root, source_path),
            "artifact": db.artifact,
            "publishable": db.publishable,
            "protect_existing_deployed_database": db.protect_existing_deployed_database,
        },
        "artifact_sha256": artifact_sha256,
        "resources": resources,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_resources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT id, title, slug, content, status, published_at, updated_at
            FROM blog_posts
            WHERE status = 'published'
            ORDER BY id
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise SQLitePublishError("SQLite source DB is missing required blog_posts schema.") from exc

    resources: list[dict[str, Any]] = []
    for row in rows:
        payload = {
            "id": str(row[0]),
            "title": str(row[1]),
            "slug": str(row[2]),
            "content": str(row[3]),
            "status": str(row[4]),
            "published_at": row[5],
            "updated_at": str(row[6]),
        }
        resource_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        resources.append(
            {
                "id": f"blog_posts:{payload['id']}",
                "kind": "record",
                "table": "blog_posts",
                "recordId": payload["id"],
                "slug": payload["slug"],
                "status": payload["status"],
                "hash": resource_hash,
                "publishedAt": payload["published_at"],
                "updatedAt": payload["updated_at"],
            }
        )
    return resources


def _changed_resources(existing_manifest: dict[str, Any] | None, current_manifest: dict[str, Any]) -> list[str]:
    if not existing_manifest:
        return [resource["id"] for resource in current_manifest.get("resources", [])]
    old = {
        str(resource.get("id")): str(resource.get("hash"))
        for resource in existing_manifest.get("resources", [])
        if isinstance(resource, dict)
    }
    changed: list[str] = []
    for resource in current_manifest.get("resources", []):
        if not isinstance(resource, dict):
            continue
        resource_id = str(resource.get("id") or "")
        if old.get(resource_id) != str(resource.get("hash") or ""):
            changed.append(resource_id)
    current_ids = {str(resource.get("id") or "") for resource in current_manifest.get("resources", []) if isinstance(resource, dict)}
    removed = sorted(resource_id for resource_id in old if resource_id and resource_id not in current_ids)
    return changed + removed


def _manifest_owns_database(manifest: dict[str, Any], project: WebsiteProject, db: SQLiteDatabaseConnection) -> bool:
    database = manifest.get("database")
    site = manifest.get("site")
    if not isinstance(database, dict) or not isinstance(site, dict):
        return False
    return (
        str(site.get("id") or "") == project.id
        and str(database.get("connection") or "") == db.name
        and str(database.get("adapter") or "").lower() == "sqlite"
    )


def _connection_by_name(project: WebsiteProject, connection: str) -> SQLiteDatabaseConnection:
    clean = _validate_connection_name(connection)
    for db in sqlite_database_connections(project):
        if db.name == clean:
            return db
    raise SQLitePublishError(f"Website {project.id!r} has no SQLite database connection named {clean!r}.")


def _validate_connection_name(value: object) -> str:
    clean = str(value or "").strip()
    if not clean or not clean.replace("_", "-").replace("-", "").isalnum():
        raise SQLitePublishError(f"Unsafe SQLite database connection name: {value!r}")
    return clean


def _validate_artifact_path(value: object) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SQLitePublishError(f"Unsafe SQLite publish artifact path: {value!r}")
    return path.as_posix()


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _repo_relative_or_abs(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _posix_relative_to(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()

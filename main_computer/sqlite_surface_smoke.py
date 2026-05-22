#!/usr/bin/env python3
"""
SQLite surface smoke battery for Main Computer.

Run from the repository root:

    python sqlite_surface_smoke.py

Optional:

    python sqlite_surface_smoke.py --keep-workdir
    python sqlite_surface_smoke.py --strict-target-gaps

What this tests:
- The current Python/backend SQLite publish helper surface.
- Direct sqlite3 connectivity to source, artifact, staged, and simulated remote DBs.
- Protective overwrite behavior.
- Whether website publish invokes SQLite publish before the deploy command.
- Known target gaps around runtime /app/data volume/env/bootstrap.

This script intentionally creates a throwaway repo_root under a temp directory for
the smoke data. It imports code from your real repo, but it should not modify your
real runtime/websites data.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Smoke:
    def __init__(self, *, strict_target_gaps: bool = False) -> None:
        self.checks: list[Check] = []
        self.strict_target_gaps = strict_target_gaps

    def pass_(self, name: str, detail: str = "", **data: Any) -> None:
        self.checks.append(Check(name, "PASS", detail, data))

    def fail(self, name: str, detail: str = "", **data: Any) -> None:
        self.checks.append(Check(name, "FAIL", detail, data))

    def warn(self, name: str, detail: str = "", **data: Any) -> None:
        status = "FAIL" if self.strict_target_gaps else "WARN"
        self.checks.append(Check(name, status, detail, data))

    def skip(self, name: str, detail: str = "", **data: Any) -> None:
        self.checks.append(Check(name, "SKIP", detail, data))

    def expect(self, name: str, fn: Callable[[], Any], detail: str = "") -> Any:
        try:
            value = fn()
        except Exception as exc:
            self.fail(name, f"{detail}\n{type(exc).__name__}: {exc}".strip(), traceback=traceback.format_exc())
            return None
        self.pass_(name, detail)
        return value

    def expect_error(self, name: str, fn: Callable[[], Any], contains: str | None = None) -> None:
        try:
            fn()
        except Exception as exc:
            message = str(exc)
            if contains and contains not in message:
                self.fail(name, f"Raised {type(exc).__name__}, but message did not contain {contains!r}: {message}")
            else:
                self.pass_(name, f"Raised expected {type(exc).__name__}: {message}")
            return
        self.fail(name, "Expected an exception, but the call succeeded.")

    def summary(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for check in self.checks:
            totals[check.status] = totals.get(check.status, 0) + 1
        return totals

    def exit_code(self) -> int:
        return 1 if any(check.status == "FAIL" for check in self.checks) else 0

    def print_text(self) -> None:
        print("\nSQLite surface smoke battery")
        print("=" * 34)
        for idx, check in enumerate(self.checks, 1):
            print(f"{idx:02d}. [{check.status}] {check.name}")
            if check.detail:
                print(indent(check.detail, "    "))
            if check.data:
                print(indent(json.dumps(check.data, indent=2, sort_keys=True, default=str), "    "))
        print("\nSummary:", json.dumps(self.summary(), sort_keys=True))
        if self.exit_code():
            print("\nResult: FAIL")
        else:
            print("\nResult: PASS")


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def table_names(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
    return [str(row[0]) for row in rows]


def fetch_blog_title(path: Path, post_id: str = "post_001") -> str:
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT title FROM blog_posts WHERE id = ?", (post_id,)).fetchone()
    if row is None:
        raise AssertionError(f"No blog_posts row found for {post_id!r} in {path}")
    return str(row[0])


def update_blog_title(path: Path, title: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            UPDATE blog_posts
            SET title = ?,
                updated_at = '2026-01-02T00:00:00.000Z'
            WHERE id = 'post_001'
            """,
            (title,),
        )
        conn.commit()


def insert_extra_post(path: Path, post_id: str, slug: str, title: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO blog_posts
              (id, title, slug, content, status, published_at, updated_at)
            VALUES
              (?, ?, ?, 'Extra smoke-test content.', 'published',
               '2026-01-03T00:00:00.000Z', '2026-01-03T00:00:00.000Z')
            """,
            (post_id, title, slug),
        )
        conn.commit()


def make_unknown_remote_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE remote_only_posts (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
        conn.execute("INSERT INTO remote_only_posts (id, title) VALUES ('live_001', 'Do not overwrite me')")
        conn.commit()


def mutate_site_manifest(repo_root: Path, site_id: str, mutator: Callable[[dict[str, Any]], None]) -> None:
    site_json = repo_root / "runtime" / "websites" / site_id / "site.json"
    payload = read_json(site_json)
    mutator(payload)
    write_json(site_json, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the current SQLite publish surface.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to the Main Computer repo whose Python modules should be imported. Default: current directory.",
    )
    parser.add_argument(
        "--workdir",
        default="",
        help="Optional throwaway workdir. Default: create a temp directory.",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Do not delete the throwaway smoke repo after the run.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    parser.add_argument(
        "--strict-target-gaps",
        action="store_true",
        help="Treat missing future deploy/runtime/API integration pieces as failures instead of warnings.",
    )
    args = parser.parse_args()

    real_repo_root = Path(args.repo_root).resolve()
    sys.path.insert(0, str(real_repo_root))

    smoke = Smoke(strict_target_gaps=args.strict_target_gaps)

    try:
        from main_computer.sqlite_publish import (
            SQLitePublishError,
            configure_sqlite_database_resource,
            ensure_sqlite_publish_smoke_source,
            publish_site_sqlite_databases,
            sqlite_database_connections,
        )
        from main_computer.website_project_manifest import (
            create_website_project,
            load_website_project,
            publish_website,
        )
        import main_computer.website_project_manifest as website_project_manifest
    except Exception as exc:
        smoke.fail(
            "import current SQLite/website backend surface",
            f"Could not import expected modules from {real_repo_root}\n{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
        )
        if args.json:
            print(json.dumps({"checks": [c.__dict__ for c in smoke.checks], "summary": smoke.summary()}, indent=2))
        else:
            smoke.print_text()
        return smoke.exit_code()

    smoke.pass_("import current SQLite/website backend surface", str(real_repo_root))

    temp_root = Path(args.workdir).resolve() if args.workdir else Path(tempfile.mkdtemp(prefix="mc-sqlite-smoke-")).resolve()
    if args.workdir:
        temp_root.mkdir(parents=True, exist_ok=True)

    smoke_repo = temp_root / "repo"
    if smoke_repo.exists():
        shutil.rmtree(smoke_repo)
    smoke_repo.mkdir(parents=True)

    try:
        site_id = "hub-site"

        create_website_project(smoke_repo, site_id, "Hub Site", kind="hub-site")
        smoke.pass_("create throwaway website project", f"{smoke_repo / 'runtime' / 'websites' / site_id}")

        project = configure_sqlite_database_resource(smoke_repo, site_id)
        manifest_conn = (
            project.manifest.get("backend", {})
            .get("databases", {})
            .get("connections", {})
            .get("content", {})
        )
        expected_defaults = {
            "adapter": "sqlite",
            "path": "./data/content.sqlite",
            "artifact": "data/content.sqlite",
            "publishable": True,
            "protect_existing_deployed_database": True,
            "existing_remote_behavior": "refuse_unknown",
        }
        if manifest_conn == expected_defaults:
            smoke.pass_("persist SQLite requirement in site manifest", "Default helper fields matched current installed surface.", manifest=manifest_conn)
        else:
            smoke.fail("persist SQLite requirement in site manifest", "Default helper fields did not match expected current surface.", manifest=manifest_conn)

        if manifest_conn.get("path") != "/app/data/content.sqlite":
            smoke.warn(
                "runtime path is not represented as /app/data/content.sqlite yet",
                "Current installed surface stores an authoring/source DB path in `backend.databases.connections.content.path`."
                " A later API may need a separate runtime_path field instead of overloading `path`.",
                current_path=manifest_conn.get("path"),
                target_runtime_path="/app/data/content.sqlite",
            )

        conns = sqlite_database_connections(load_website_project(smoke_repo, site_id))
        if len(conns) == 1 and conns[0].name == "content" and conns[0].adapter == "sqlite":
            smoke.pass_("parse SQLite database connections from manifest")
        else:
            smoke.fail("parse SQLite database connections from manifest", "Expected one SQLite connection named content.", connections=[str(c) for c in conns])

        missing_dry_run = publish_site_sqlite_databases(smoke_repo, site_id, lane="local", dry_run=True)
        if missing_dry_run and missing_dry_run[0].get("source_exists") is False and missing_dry_run[0].get("would_error"):
            smoke.pass_("dry-run missing source DB reports would_error instead of writing", data=missing_dry_run[0])
        else:
            smoke.fail("dry-run missing source DB reports would_error instead of writing", data={"result": missing_dry_run})

        source_result = ensure_sqlite_publish_smoke_source(smoke_repo, site_id)
        source_db = smoke_repo / "runtime" / "websites" / site_id / "data" / "content.sqlite"
        if source_db.is_file() and fetch_blog_title(source_db) == "Hello Blog":
            smoke.pass_("create/connect/read source SQLite DB", "Connected with sqlite3 and read seeded post.", source=str(source_db), source_result=source_result)
        else:
            smoke.fail("create/connect/read source SQLite DB", "Source DB was not created or did not contain expected seed row.")

        before_tables = table_names(source_db)
        ensure_sqlite_publish_smoke_source(smoke_repo, site_id)
        after_tables = table_names(source_db)
        if before_tables == after_tables and fetch_blog_title(source_db) == "Hello Blog":
            smoke.pass_("source bootstrap is non-destructive on second run", tables=after_tables)
        else:
            smoke.fail("source bootstrap is non-destructive on second run", before=before_tables, after=after_tables)

        first_publish = publish_site_sqlite_databases(smoke_repo, site_id, lane="local")
        artifact_db = smoke_repo / "runtime" / "websites" / site_id / "dist" / "data" / "content.sqlite"
        manifest_path = artifact_db.parent / "publish-manifest.json"
        if (
            first_publish
            and first_publish[0].get("action") == "created"
            and artifact_db.is_file()
            and manifest_path.is_file()
            and fetch_blog_title(artifact_db) == "Hello Blog"
        ):
            smoke.pass_("first SQLite publish creates readable artifact and manifest", result=first_publish[0])
        else:
            smoke.fail(
                "first SQLite publish creates readable artifact and manifest",
                result={"publish": first_publish, "artifact_exists": artifact_db.is_file(), "manifest_exists": manifest_path.is_file()},
            )

        second_publish = publish_site_sqlite_databases(smoke_repo, site_id, lane="local")
        if second_publish and second_publish[0].get("action") == "unchanged" and second_publish[0].get("logical_changes") is False:
            smoke.pass_("second publish is unchanged and does not overwrite", result=second_publish[0])
        else:
            smoke.fail("second publish is unchanged and does not overwrite", result={"publish": second_publish})

        update_blog_title(source_db, "Hello Blog Updated")
        staged_publish = publish_site_sqlite_databases(smoke_repo, site_id, lane="local")
        staged_db = smoke_repo / staged_publish[0].get("staged_artifact", "__missing__") if staged_publish else smoke_repo / "__missing__"
        if (
            staged_publish
            and staged_publish[0].get("action") == "staged"
            and fetch_blog_title(artifact_db) == "Hello Blog"
            and staged_db.is_file()
            and fetch_blog_title(staged_db) == "Hello Blog Updated"
        ):
            smoke.pass_(
                "changed source DB stages instead of overwriting protected published DB",
                result=staged_publish[0],
                live_artifact_title=fetch_blog_title(artifact_db),
                staged_title=fetch_blog_title(staged_db),
            )
        else:
            smoke.fail("changed source DB stages instead of overwriting protected published DB", result={"publish": staged_publish})

        insert_extra_post(source_db, "post_002", "second-post", "Second Post")
        staged_publish_two_posts = publish_site_sqlite_databases(smoke_repo, site_id, lane="local")
        changed = staged_publish_two_posts[0].get("changed_resources", []) if staged_publish_two_posts else []
        if "blog_posts:post_002" in changed:
            smoke.pass_("new published rows are detected as changed resources", result=staged_publish_two_posts[0])
        else:
            smoke.fail("new published rows are detected as changed resources", result={"changed_resources": changed, "publish": staged_publish_two_posts})

        remote_root = smoke_repo / "remote-deploy-target"
        unknown_remote_db = remote_root / "data" / "content.sqlite"
        make_unknown_remote_db(unknown_remote_db)
        try:
            publish_site_sqlite_databases(smoke_repo, site_id, lane="deploy", output_root=remote_root)
            smoke.fail("unknown existing remote DB is refused", "Expected SQLitePublishError, but publish succeeded.")
        except SQLitePublishError as exc:
            with sqlite3.connect(unknown_remote_db) as conn:
                row = conn.execute("SELECT title FROM remote_only_posts WHERE id = 'live_001'").fetchone()
            if row == ("Do not overwrite me",):
                smoke.pass_("unknown existing remote DB is refused and preserved", str(exc))
            else:
                smoke.fail("unknown existing remote DB is refused and preserved", "Remote-only row was not preserved.", row=row)

        forced = publish_site_sqlite_databases(smoke_repo, site_id, lane="deploy", output_root=remote_root, force=True)
        if forced and forced[0].get("action") in {"created", "replaced"} and fetch_blog_title(unknown_remote_db) == "Hello Blog Updated":
            if "remote_only_posts" not in table_names(unknown_remote_db):
                smoke.pass_("force=True can replace unknown remote DB explicitly", result=forced[0], tables=table_names(unknown_remote_db))
            else:
                smoke.fail("force=True can replace unknown remote DB explicitly", "remote_only_posts table still exists after force replace.")
        else:
            smoke.fail("force=True can replace unknown remote DB explicitly", result={"publish": forced})

        # Path safety and path-shape probes.
        bad_source_site = "bad-source-site"
        create_website_project(smoke_repo, bad_source_site, "Bad Source Site")
        configure_sqlite_database_resource(smoke_repo, bad_source_site, path="../escape.sqlite")
        smoke.expect_error(
            "source path traversal is blocked at DB operation time",
            lambda: ensure_sqlite_publish_smoke_source(smoke_repo, bad_source_site),
            contains="may not contain '..'",
        )

        abs_runtime_site = "runtime-path-site"
        create_website_project(smoke_repo, abs_runtime_site, "Runtime Path Site")
        configure_sqlite_database_resource(smoke_repo, abs_runtime_site, path="/app/data/content.sqlite")
        smoke.expect_error(
            "absolute /app/data path is not accepted as current source path",
            lambda: ensure_sqlite_publish_smoke_source(smoke_repo, abs_runtime_site),
            contains="must be site-relative",
        )

        bad_artifact_site = "bad-artifact-site"
        create_website_project(smoke_repo, bad_artifact_site, "Bad Artifact Site")
        configure_sqlite_database_resource(smoke_repo, bad_artifact_site, artifact="../escape.sqlite")
        smoke.expect_error(
            "artifact path traversal is blocked before publish",
            lambda: publish_site_sqlite_databases(smoke_repo, bad_artifact_site, lane="local", dry_run=True),
            contains="Unsafe SQLite publish artifact path",
        )

        skipped_site = "not-publishable-site"
        create_website_project(smoke_repo, skipped_site, "Not Publishable Site")
        configure_sqlite_database_resource(smoke_repo, skipped_site, publishable=False)
        skipped = publish_site_sqlite_databases(smoke_repo, skipped_site, lane="local", dry_run=True)
        if skipped == []:
            smoke.pass_("publishable=False connection is skipped by publish surface")
        else:
            smoke.fail("publishable=False connection is skipped by publish surface", result={"publish": skipped})

        # Fake the docker deploy command so this tests ordering without requiring Docker.
        publish_site = "deploy-order-site"
        create_website_project(smoke_repo, publish_site, "Deploy Order Site")
        configure_sqlite_database_resource(smoke_repo, publish_site)
        ensure_sqlite_publish_smoke_source(smoke_repo, publish_site)

        mutate_site_manifest(
            smoke_repo,
            publish_site,
            lambda payload: payload.setdefault("local_platform", {}).setdefault("lanes", {}).update(
                {
                    "local": {
                        "service": "deploy-order-local",
                        "port": 18123,
                        "url": "http://127.0.0.1:18123/",
                        "status_url": "http://127.0.0.1:18123/api/site/status",
                    }
                }
            ),
        )

        calls: list[list[str]] = []
        original_run = website_project_manifest.subprocess.run

        def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
            calls.append([str(part) for part in command])
            return SimpleNamespace(returncode=0, stdout="fake docker compose up\n", stderr="")

        website_project_manifest.subprocess.run = fake_run
        try:
            deploy_result = publish_website(smoke_repo, publish_site, lane="local", verify=False)
        finally:
            website_project_manifest.subprocess.run = original_run

        deploy_artifact = smoke_repo / "runtime" / "websites" / publish_site / "dist" / "data" / "content.sqlite"
        if (
            deploy_result.get("ok") is True
            and deploy_result.get("database_publish")
            and deploy_result["database_publish"][0].get("action") == "created"
            and deploy_artifact.is_file()
            and calls
            and calls[0][-1] == "deploy-order-local"
        ):
            smoke.pass_(
                "publish_website invokes SQLite publish before deploy command",
                result=deploy_result,
                fake_docker_command=calls[0],
            )
        else:
            smoke.fail(
                "publish_website invokes SQLite publish before deploy command",
                result={"deploy": deploy_result, "calls": calls, "artifact_exists": deploy_artifact.is_file()},
            )

        generated_compose = smoke_repo / "deploy" / "local-platform" / "generated" / "docker-compose.websites.yml"
        compose_text = generated_compose.read_text(encoding="utf-8") if generated_compose.exists() else ""
        has_runtime_mount = "/app/data" in compose_text
        has_database_env = "DATABASE_PATH" in compose_text or "DATABASE_ADAPTER" in compose_text
        if has_runtime_mount and has_database_env:
            smoke.pass_("generated compose contains SQLite runtime mount/env", path=str(generated_compose))
        else:
            smoke.warn(
                "generated compose does not yet provision SQLite runtime storage",
                "Current compose generation appears to publish a dist artifact, but it does not yet mount /app/data"
                " or pass DATABASE_PATH/DATABASE_ADAPTER into the site container.",
                path=str(generated_compose),
                has_runtime_mount=has_runtime_mount,
                has_database_env=has_database_env,
            )

        site_server_app = real_repo_root / "deploy" / "local-platform" / "site-server" / "app.py"
        site_server_text = site_server_app.read_text(encoding="utf-8") if site_server_app.exists() else ""
        if "sqlite3" in site_server_text and "DATABASE_PATH" in site_server_text:
            smoke.pass_("site-server runtime appears to know how to initialize SQLite")
        else:
            smoke.warn(
                "site-server runtime does not yet expose SQLite bootstrap/status",
                "No sqlite3/DATABASE_PATH runtime bootstrap was detected in deploy/local-platform/site-server/app.py.",
                path=str(site_server_app),
            )

        route_files = [
            real_repo_root / "main_computer" / "viewport_routes_applications.py",
            real_repo_root / "main_computer" / "viewport_route_dispatch.py",
        ]
        route_blob = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in route_files if path.exists())
        has_sqlite_route = "sqlite" in route_blob.lower()
        has_database_route = "database" in route_blob.lower() and "websites/site" in route_blob
        if has_sqlite_route or has_database_route:
            smoke.pass_("application route surface mentions database/sqlite")
        else:
            smoke.warn(
                "no obvious frontend/API route for adding SQLite requirement yet",
                "The helper exists, but the web/API route surface does not obviously expose it yet.",
                files=[str(path) for path in route_files],
            )

    finally:
        if args.keep_workdir or args.workdir:
            smoke.pass_("kept smoke workdir", str(temp_root))
        else:
            shutil.rmtree(temp_root, ignore_errors=True)

    if args.json:
        print(
            json.dumps(
                {
                    "checks": [
                        {
                            "name": c.name,
                            "status": c.status,
                            "detail": c.detail,
                            "data": c.data,
                        }
                        for c in smoke.checks
                    ],
                    "summary": smoke.summary(),
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
    else:
        smoke.print_text()

    return smoke.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())

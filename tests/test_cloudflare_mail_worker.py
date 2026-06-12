from __future__ import annotations

import importlib.util
import json
import py_compile
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "cloudflare_mail_worker.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("cloudflare_mail_worker", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_plan_normalizes_coolify_ui_url_and_defaults_to_mail_ingest_host() -> None:
    module = _load_module()

    plan = module.build_plan(domain="greatlibrary.io", coolify_url="http://144.126.212.9:8000/projects")

    assert plan.domain == "greatlibrary.io"
    assert plan.ingest_host == "mail-ingest.greatlibrary.io"
    assert plan.ingest_url == "https://mail-ingest.greatlibrary.io/inbound/cloudflare-email"
    assert plan.coolify_url == "http://144.126.212.9:8000"
    assert plan.worker_name == "greatlibrary.io-mail-worker"
    assert plan.catch_all is True
    assert any("Cloudflare Email Routing" in warning for warning in plan.warnings)


def test_worker_source_posts_raw_rfc822_stream_to_https_ingest() -> None:
    module = _load_module()
    plan = module.build_plan(domain="greatlibrary.io", ingest_host="mail-ingest.greatlibrary.io")
    source = module.render_worker_source(plan)

    assert "async email(message: ForwardableEmailMessage" in source
    assert 'headers.set("Content-Type", "message/rfc822")' in source
    assert 'headers.set("X-Envelope-From", message.from)' in source
    assert 'headers.set("X-Envelope-To", message.to)' in source
    assert "body: message.raw" in source
    assert "await fetch(ingestUrl" in source
    assert "message.setReject(\"message too large\")" in source


def test_compose_is_self_contained_and_does_not_publish_raw_mail_ports() -> None:
    module = _load_module()
    plan = module.build_plan(domain="greatlibrary.io")
    compose = module.render_compose(plan)

    assert "image: python:3.12-alpine" in compose
    assert "cat > /tmp/mail_ingest.py <<'PY'" in compose
    assert "MAIL_INGEST_SECRET:" in compose
    assert 'MAIL_DOMAIN: "greatlibrary.io"' in compose
    assert 'expose:\n      - "8080"' in compose
    assert "ports:" not in compose
    assert ":25:25" not in compose
    assert "mail-ingest-mailboxes:/var/mail" in compose


def test_ingest_app_compiles_and_contains_maildir_delivery(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan(domain="greatlibrary.io")
    app_path = tmp_path / "mail_ingest.py"
    app_path.write_text(module.render_ingest_app(plan), encoding="utf-8")

    py_compile.compile(str(app_path), doraise=True)

    app_text = app_path.read_text(encoding="utf-8")
    assert 'self.headers.get("X-Envelope-To", "")' in app_text
    assert 'MAILBOX_ROOT / MAIL_DOMAIN / local / "Maildir"' in app_text
    assert 'json_response(self, 201, result)' in app_text


def test_write_outputs_creates_worker_and_coolify_artifacts(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan(domain="greatlibrary.io", local_parts=["admin,postmaster"], catch_all=False)

    module.write_outputs(plan, tmp_path)

    assert (tmp_path / "worker" / "src" / "index.ts").exists()
    assert (tmp_path / "worker" / "wrangler.toml").exists()
    assert (tmp_path / "worker" / "package.json").exists()
    assert (tmp_path / "coolify" / "compose.yaml").exists()
    assert (tmp_path / "coolify" / "mail_ingest.py").exists()
    assert "admin@greatlibrary.io" in (tmp_path / "cloudflare-routing.md").read_text(encoding="utf-8")
    plan_json = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert plan_json["schema"] == "main-computer.cloudflare-mail-worker.v1"
    assert plan_json["local_parts"] == ["admin", "postmaster"]


def test_validate_refuses_no_catch_all_without_local_parts() -> None:
    module = _load_module()

    plan = module.build_plan(domain="greatlibrary.io", catch_all=False)
    try:
        module.validate_plan(plan)
    except module.WorkerPlanError as exc:
        assert "catch-all" in str(exc)
    else:
        raise AssertionError("plan should require catch-all or explicit local parts")

def test_parse_args_exposes_coolify_deployer_options() -> None:
    module = _load_module()

    args = module.parse_args([
        "apply",
        "--domain", "greatlibrary.io",
        "--coolify-url", "http://144.126.212.9:8000/projects",
        "--coolify-token-env", "MAIN_COMPUTER_COOLIFY_TOKEN",
        "--coolify-project-name", "Default",
        "--coolify-server-name", "main-server",
        "--coolify-environment", "mail",
        "--coolify-service-name", "greatlibrary-mail-ingest",
        "--dry-run",
    ])

    assert args.action == "apply"
    assert args.coolify_token_env == "MAIN_COMPUTER_COOLIFY_TOKEN"
    assert args.coolify_project_name == "Default"
    assert args.coolify_server_name == "main-server"
    assert args.coolify_environment == "mail"
    assert args.coolify_service_name == "greatlibrary-mail-ingest"
    assert args.dry_run is True


def test_coolify_dry_run_sync_uses_normalized_url_and_service_name() -> None:
    module = _load_module()

    args = module.parse_args([
        "coolify-sync",
        "--domain", "greatlibrary.io",
        "--coolify-url", "http://144.126.212.9:8000/projects",
        "--coolify-service-name", "greatlibrary-mail-ingest",
        "--coolify-environment", "mail",
        "--dry-run",
    ])
    plan = module.plan_from_args(args)
    result = module.coolify_sync(plan, args, deploy=False)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["service_name"] == "greatlibrary-mail-ingest"
    assert result["coolify_url"] == "http://144.126.212.9:8000"
    assert result["coolify_environment"] == "mail"
    assert "docker_compose_raw" not in result
    assert "MAIL_INGEST_SECRET" in result["compose"]


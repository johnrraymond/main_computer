from __future__ import annotations

import importlib.util
import os
import stat
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "coolify_mail_server.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("coolify_mail_server", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_plan_defaults_to_mail_subdomain_and_public_mail_ports() -> None:
    module = _load_module()

    plan = module.build_plan("production", domain="example.net", target_address="203.0.113.10")

    assert plan.domain == "example.net"
    assert plan.mail_host == "mail.example.net"
    assert plan.target_address == "203.0.113.10"
    assert {port.host_port for port in plan.ports} == {25, 465, 587, 143, 993, 110, 995}
    assert all(port.bind_host == "0.0.0.0" for port in plan.ports)
    assert any(record.type == "A" and record.name == "mail" and record.proxied is False for record in plan.dns_records)
    assert any(record.type == "MX" and record.content == "mail.example.net" for record in plan.dns_records)
    assert any("Cloudflare" in warning for warning in plan.warnings)


def test_compose_embeds_environment_and_direct_port_mappings_for_coolify_api_push() -> None:
    module = _load_module()

    plan = module.build_plan("production", domain="example.net", target_address="203.0.113.10")
    compose = module.render_compose(plan)

    assert "env_file:" not in compose
    assert 'hostname: "mail.example.net"' in compose
    assert "    environment:" in compose
    assert '      OVERRIDE_HOSTNAME: "mail.example.net"' in compose
    assert '      ENABLE_IMAP: "1"' in compose
    assert '      ENABLE_POP3: "1"' in compose
    assert '      PERMIT_DOCKER: "none"' in compose
    assert '      - "0.0.0.0:25:25"' in compose
    assert '      - "0.0.0.0:993:993"' in compose
    assert '      - "0.0.0.0:995:995"' in compose
    assert '      - "main-computer-mail-config:/tmp/docker-mailserver/"' in compose
    assert '      - "/etc/letsencrypt:/etc/letsencrypt:ro"' in compose
    assert "ss -ltn | grep -q ':25 '" in compose


def test_no_pop3_disables_pop3_ports_and_environment() -> None:
    module = _load_module()

    plan = module.build_plan("production", domain="example.net", target_address="203.0.113.10", enable_pop3=False)
    compose = module.render_compose(plan)
    env_text = module.render_env(plan)

    assert {port.host_port for port in plan.ports} == {25, 465, 587, 143, 993}
    assert '      - "0.0.0.0:110:110"' not in compose
    assert '      - "0.0.0.0:995:995"' not in compose
    assert '      ENABLE_POP3: "0"' in compose
    assert "ENABLE_POP3=0" in env_text


def test_cloudflare_dns01_mode_adds_profiled_certbot_service() -> None:
    module = _load_module()

    plan = module.build_plan(
        "production",
        domain="example.net",
        target_address="203.0.113.10",
        tls_mode="cloudflare-dns01",
    )
    compose = module.render_compose(plan)

    assert "  certbot-cloudflare:" in compose
    assert "      - certbot" in compose
    assert "      - --dns-cloudflare" in compose
    assert '      - "./secrets/cloudflare.ini:/run/secrets/cloudflare.ini:ro"' in compose
    assert "/etc/letsencrypt:/etc/letsencrypt:ro" not in compose
    assert "main-computer-mail-certs:/etc/letsencrypt/:ro" in compose


def test_validate_for_apply_refuses_placeholders_and_accepts_real_target() -> None:
    module = _load_module()

    placeholder = module.build_plan("production")
    try:
        module.validate_for_apply(placeholder)
    except module.PlanError as exc:
        assert "example.com" in str(exc)
    else:
        raise AssertionError("placeholder plan should not be deployable")

    plan = module.build_plan("production", domain="example.net", target_address="203.0.113.10")
    module.validate_for_apply(plan)


def test_write_outputs_creates_operator_artifacts(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("production", domain="example.net", target_address="203.0.113.10")

    module.write_outputs(plan, tmp_path)

    assert (tmp_path / "compose.yaml").read_text(encoding="utf-8").startswith("name: main-computer-mail")
    assert "ENABLE_POP3=1" in (tmp_path / "mailserver.env").read_text(encoding="utf-8")
    records = json.loads((tmp_path / "cloudflare-records.json").read_text(encoding="utf-8"))
    assert records["zone"] == "example.net"
    assert any(record["type"] == "MX" for record in records["records"])
    assert "setup config dkim domain example.net" in (tmp_path / "operator-commands.txt").read_text(encoding="utf-8")
    verify_script = tmp_path / "verify-mail-server.sh"
    assert verify_script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    if os.name != "nt":
        assert verify_script.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

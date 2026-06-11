#!/usr/bin/env python3
"""Plan, render, and optionally push a Coolify-managed Docker Mailserver stack.

This deployer is intentionally self-contained, like the other Coolify-centric
operators in this repository. It renders a raw Docker Compose resource that
binds SMTP/IMAP/POP3 ports directly on the Coolify host, because mail protocols
cannot be served through the normal HTTP reverse proxy path.
"""

from __future__ import annotations

import argparse
import base64
import copy
import ipaddress
import json
import os
import re
import shlex
import socket
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_DMS_IMAGE = "ghcr.io/docker-mailserver/docker-mailserver:latest"
DEFAULT_CERTBOT_IMAGE = "certbot/dns-cloudflare:latest"
DEFAULT_COOLIFY_TOKEN_ENV = "MAIN_COMPUTER_COOLIFY_TOKEN"
DEFAULT_COOLIFY_API_TIMEOUT_S = 25.0
DEFAULT_COOLIFY_API_RETRIES = 1
DEFAULT_COOLIFY_API_RETRY_SLEEP_S = 2.0

SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}\.?$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$", re.IGNORECASE)


MAIL_SERVER_SEEDS: dict[str, dict[str, Any]] = {
    "production": {
        "description": "Single-host Docker Mailserver stack managed as a Coolify raw Docker Compose service.",
        "domain": "example.com",
        "mail_host": "mail.example.com",
        "target_address": "SERVER_IP",
        "compose_project": "main-computer-mail",
        "service_name": "main-computer-mail",
        "docker_image": DEFAULT_DMS_IMAGE,
        "certbot_image": DEFAULT_CERTBOT_IMAGE,
        "bind_host": "0.0.0.0",
        "enable_pop3": True,
        "enable_clamav": True,
        "enable_rspamd": True,
        "enable_fail2ban": True,
        "tls_mode": "host-letsencrypt",
        "postmaster": "",
        "admin_mailbox": "",
        "mailbox_quota": "0",
        "message_size_limit": "52428800",
        "rspamd_greylisting": True,
        "rspamd_learn": True,
        "ipv4_only": True,
        "cloudflare": {
            "enabled": True,
            "ttl": 1,
            "spf": "v=spf1 mx -all",
            "dmarc": "v=DMARC1; p=quarantine; rua=mailto:postmaster@example.com; adkim=s; aspf=s",
        },
    }
}


class PlanError(ValueError):
    """Raised when a mail server plan cannot be rendered safely."""


class CoolifyMailError(RuntimeError):
    """Raised when the Coolify API cannot perform the requested operation."""


@dataclass(frozen=True)
class MailPort:
    id: str
    host_port: int
    container_port: int
    bind_host: str
    protocol: str
    enabled: bool
    purpose: str


@dataclass(frozen=True)
class DnsRecord:
    type: str
    name: str
    content: str
    ttl: int = 1
    priority: int | None = None
    proxied: bool | None = None
    comment: str = ""


@dataclass(frozen=True)
class MailServerPlan:
    name: str
    description: str
    domain: str
    mail_host: str
    target_address: str
    compose_project: str
    service_name: str
    docker_image: str
    certbot_image: str
    bind_host: str
    enable_pop3: bool
    enable_clamav: bool
    enable_rspamd: bool
    enable_fail2ban: bool
    tls_mode: str
    postmaster: str
    admin_mailbox: str
    mailbox_quota: str
    message_size_limit: str
    rspamd_greylisting: bool
    rspamd_learn: bool
    ipv4_only: bool
    cloudflare_ttl: int
    spf_record: str
    dmarc_record: str
    ports: tuple[MailPort, ...]
    dns_records: tuple[DnsRecord, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "main-computer.coolify-mail-server.v1",
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "mail_host": self.mail_host,
            "target_address": self.target_address,
            "compose_project": self.compose_project,
            "service_name": self.service_name,
            "docker_image": self.docker_image,
            "certbot_image": self.certbot_image,
            "bind_host": self.bind_host,
            "enable_pop3": self.enable_pop3,
            "enable_clamav": self.enable_clamav,
            "enable_rspamd": self.enable_rspamd,
            "enable_fail2ban": self.enable_fail2ban,
            "tls_mode": self.tls_mode,
            "postmaster": self.postmaster,
            "admin_mailbox": self.admin_mailbox,
            "mailbox_quota": self.mailbox_quota,
            "message_size_limit": self.message_size_limit,
            "rspamd_greylisting": self.rspamd_greylisting,
            "rspamd_learn": self.rspamd_learn,
            "ipv4_only": self.ipv4_only,
            "ports": [asdict(port) for port in self.ports],
            "dns_records": [asdict(record) for record in self.dns_records],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class CoolifyResponse:
    ok: bool
    status: int
    method: str
    path: str
    body: Any


class CoolifyClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = DEFAULT_COOLIFY_API_TIMEOUT_S,
        retries: int = DEFAULT_COOLIFY_API_RETRIES,
        retry_sleep_s: float = DEFAULT_COOLIFY_API_RETRY_SLEEP_S,
    ) -> None:
        clean = str(base_url or "").strip().rstrip("/")
        if not clean.startswith(("http://", "https://")):
            raise CoolifyMailError(f"Coolify URL must start with http:// or https://, got {base_url!r}.")
        self.base_url = clean
        self.token = str(token or "").strip()
        self.timeout_s = float(timeout_s)
        self.retries = max(0, int(retries))
        self.retry_sleep_s = max(0.0, float(retry_sleep_s))

    def request(self, method: str, path: str, payload: Any | None = None) -> CoolifyResponse:
        api_path = path if path.startswith("/") else f"/{path}"
        url = self.base_url + api_path
        data = None
        headers = {
            "Accept": "application/json,text/plain,*/*",
            "Authorization": f"Bearer {self.token}",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempts = self.retries + 1
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    return CoolifyResponse(
                        ok=200 <= int(response.status) < 300,
                        status=int(response.status),
                        method=method.upper(),
                        path=api_path,
                        body=parse_response_body(raw),
                    )
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                return CoolifyResponse(
                    ok=False,
                    status=int(exc.code),
                    method=method.upper(),
                    path=api_path,
                    body=parse_response_body(raw),
                )
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(self.retry_sleep_s)

        return CoolifyResponse(
            ok=False,
            status=0,
            method=method.upper(),
            path=api_path,
            body={
                "error": "request_failed",
                "message": f"Coolify API request failed: {url}: {last_error}",
                "error_type": type(last_error).__name__ if last_error is not None else "unknown",
            },
        )


def parse_response_body(raw: str) -> Any:
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def response_to_dict(response: CoolifyResponse, *, token: str = "", token_source: str = "") -> dict[str, Any]:
    data = {
        "ok": response.ok,
        "status": response.status,
        "method": response.method,
        "path": response.path,
        "body": response.body,
    }
    if token:
        data["token"] = redact_secret(token)
    if token_source:
        data["token_source"] = token_source
    return data


def body_items(body: Any, *preferred_keys: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    for key in (*preferred_keys, "data", "items", "services", "projects", "servers", "environments", "resources"):
        value = body.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def coolify_item_uuid(item: dict[str, Any]) -> str:
    for key in ("uuid", "id", "service_uuid", "project_uuid", "server_uuid", "environment_uuid"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def coolify_item_name(item: dict[str, Any]) -> str:
    for key in ("name", "description", "fqdn", "urls"):
        value = item.get(key)
        if isinstance(value, list) and value:
            return str(value[0]).strip()
        text = str(value or "").strip()
        if text:
            return text
    return ""


def coolify_item_summary(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "uuid",
        "id",
        "name",
        "description",
        "fqdn",
        "urls",
        "status",
        "project_uuid",
        "server_uuid",
        "environment_name",
    )
    summary = {key: item.get(key) for key in keys if item.get(key) not in (None, "")}
    if "uuid" not in summary:
        uuid = coolify_item_uuid(item)
        if uuid:
            summary["uuid"] = uuid
    if "name" not in summary:
        name = coolify_item_name(item)
        if name:
            summary["name"] = name
    return summary


def coolify_list(
    client: CoolifyClient,
    args: argparse.Namespace,
    path: str,
    *,
    label: str,
    preferred_keys: tuple[str, ...] = (),
) -> tuple[CoolifyResponse, list[dict[str, Any]]]:
    operator_log(args, "coolify-list start", label=label, path=path)
    response = client.request("GET", path)
    items = body_items(response.body, *preferred_keys) if response.ok else []
    operator_log(args, "coolify-list result", label=label, ok=response.ok, status=response.status, count=len(items))
    return response, items


def redact_secret(value: str, *, keep: int = 4) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}...{text[-keep:]}"


def operator_log(args: argparse.Namespace, message: str, **fields: Any) -> None:
    if bool(getattr(args, "quiet", False)):
        return
    details = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
    prefix = "[coolify-mail]"
    print(f"{prefix} {message}" + (f" {details}" if details else ""), file=sys.stderr)


def safe_id(value: str, *, kind: str = "id") -> str:
    clean = str(value or "").strip().lower()
    clean = re.sub(r"[^a-z0-9_.-]+", "-", clean)
    clean = re.sub(r"-+", "-", clean).strip("-._")
    if not clean:
        raise PlanError(f"Missing {kind}.")
    if len(clean) > 63:
        clean = clean[:63].rstrip("-._")
    if not SAFE_ID_RE.match(clean):
        raise PlanError(f"Unsafe {kind}: {value!r}")
    return clean


def normalize_domain(value: str, *, field: str) -> str:
    clean = str(value or "").strip().lower().rstrip(".")
    if not clean or clean in {"example.com", "mail.example.com"}:
        return clean
    if not DOMAIN_RE.match(clean):
        raise PlanError(f"{field} must be a valid DNS name, got {value!r}.")
    return clean


def is_placeholder(value: str) -> bool:
    text = str(value or "").strip().upper()
    return text in {"", "SERVER_IP", "MAIL_SERVER_IP", "EXAMPLE.COM", "MAIL.EXAMPLE.COM"}


def host_part_from_ssh(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if ":" in text and not text.startswith("["):
        # root@host:/path is not expected here, but keep just the host if supplied.
        text = text.split(":", 1)[0]
    return text.strip("[]")


def normalize_target_address(value: str) -> str:
    clean = host_part_from_ssh(value)
    if not clean:
        return "SERVER_IP"
    if clean == "SERVER_IP":
        return clean
    try:
        return str(ipaddress.ip_address(clean))
    except ValueError:
        # Also allow DNS names for unusual deployments.
        return normalize_domain(clean, field="target_address")


def root_record_name(domain: str) -> str:
    return "@"


def mail_subdomain_name(domain: str, mail_host: str) -> str:
    if mail_host == domain:
        return "@"
    suffix = "." + domain
    if mail_host.endswith(suffix):
        return mail_host[: -len(suffix)]
    return mail_host


def build_ports(bind_host: str, enable_pop3: bool) -> tuple[MailPort, ...]:
    ports = [
        MailPort("smtp", 25, 25, bind_host, "tcp", True, "SMTP server-to-server transfer"),
        MailPort("submissions", 465, 465, bind_host, "tcp", True, "SMTP submission with implicit TLS"),
        MailPort("submission", 587, 587, bind_host, "tcp", True, "SMTP submission with STARTTLS"),
        MailPort("imap", 143, 143, bind_host, "tcp", True, "IMAP with STARTTLS"),
        MailPort("imaps", 993, 993, bind_host, "tcp", True, "IMAP over TLS"),
    ]
    if enable_pop3:
        ports.extend(
            [
                MailPort("pop3", 110, 110, bind_host, "tcp", True, "POP3 with STARTTLS"),
                MailPort("pop3s", 995, 995, bind_host, "tcp", True, "POP3 over TLS"),
            ]
        )
    return tuple(ports)


def build_dns_records(
    *,
    domain: str,
    mail_host: str,
    target_address: str,
    ttl: int,
    spf_record: str,
    dmarc_record: str,
) -> tuple[DnsRecord, ...]:
    return (
        DnsRecord("A", mail_subdomain_name(domain, mail_host), target_address, ttl=ttl, proxied=False, comment="Cloudflare must be DNS only / gray cloud."),
        DnsRecord("MX", root_record_name(domain), mail_host, ttl=ttl, priority=10, comment="Primary inbound mail exchanger."),
        DnsRecord("TXT", root_record_name(domain), spf_record, ttl=ttl, comment="SPF: this server's MX is the authorized sender."),
        DnsRecord("TXT", "_dmarc", dmarc_record, ttl=ttl, comment="DMARC policy; start at quarantine and promote after proving delivery."),
        DnsRecord("TXT", "mail._domainkey", "DKIM_PUBLIC_KEY_FROM_DMS_SETUP_CONFIG_DKIM", ttl=ttl, comment="Replace after running: docker exec mailserver setup config dkim domain <domain>."),
    )


def load_seed(name_or_path: str) -> tuple[str, dict[str, Any]]:
    key = str(name_or_path or "production").strip() or "production"
    path = Path(key)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise PlanError(f"Seed file must contain a JSON object: {path}")
        return path.stem, payload
    if key not in MAIL_SERVER_SEEDS:
        raise PlanError(f"Unknown mail server seed {key!r}. Known seeds: {', '.join(sorted(MAIL_SERVER_SEEDS))}")
    return key, copy.deepcopy(MAIL_SERVER_SEEDS[key])


def build_plan(
    seed_name: str = "production",
    *,
    domain: str | None = None,
    mail_host: str | None = None,
    target_address: str | None = None,
    compose_project: str | None = None,
    service_name: str | None = None,
    docker_image: str | None = None,
    certbot_image: str | None = None,
    bind_host: str | None = None,
    enable_pop3: bool | None = None,
    tls_mode: str | None = None,
    postmaster: str | None = None,
    admin_mailbox: str | None = None,
) -> MailServerPlan:
    name, seed = load_seed(seed_name)
    seed_domain = str(seed.get("domain") or "example.com")
    selected_domain = normalize_domain(domain if domain is not None else seed_domain, field="domain")
    if not selected_domain:
        raise PlanError("Domain is required.")

    selected_mail_host = normalize_domain(
        mail_host if mail_host is not None else str(seed.get("mail_host") or f"mail.{selected_domain}"),
        field="mail_host",
    )
    if not selected_mail_host or selected_mail_host == "mail.example.com" and selected_domain != "example.com":
        selected_mail_host = f"mail.{selected_domain}"

    if not (selected_mail_host == selected_domain or selected_mail_host.endswith("." + selected_domain)):
        raise PlanError(f"mail_host {selected_mail_host!r} must be the domain itself or a subdomain of {selected_domain!r}.")

    selected_target = normalize_target_address(target_address if target_address is not None else str(seed.get("target_address") or "SERVER_IP"))
    selected_bind = str(bind_host if bind_host is not None else seed.get("bind_host") or "0.0.0.0").strip()
    if selected_bind not in {"0.0.0.0", "127.0.0.1", "::"}:
        try:
            ipaddress.ip_address(selected_bind)
        except ValueError as exc:
            raise PlanError(f"bind_host must be an IP address, got {selected_bind!r}.") from exc

    pop3_enabled = bool(seed.get("enable_pop3", True)) if enable_pop3 is None else bool(enable_pop3)
    selected_tls_mode = str(tls_mode if tls_mode is not None else seed.get("tls_mode") or "host-letsencrypt").strip().lower()
    if selected_tls_mode not in {"host-letsencrypt", "cloudflare-dns01", "manual"}:
        raise PlanError("tls_mode must be one of: host-letsencrypt, cloudflare-dns01, manual.")

    selected_postmaster = str(postmaster if postmaster is not None else seed.get("postmaster") or f"postmaster@{selected_domain}").strip()
    selected_admin = str(admin_mailbox if admin_mailbox is not None else seed.get("admin_mailbox") or f"admin@{selected_domain}").strip()
    if "@" not in selected_postmaster:
        selected_postmaster = f"{selected_postmaster}@{selected_domain}"
    if "@" not in selected_admin:
        selected_admin = f"{selected_admin}@{selected_domain}"

    cf = seed.get("cloudflare") if isinstance(seed.get("cloudflare"), dict) else {}
    ttl = int(cf.get("ttl", 1) or 1)
    spf = str(cf.get("spf") or "v=spf1 mx -all")
    dmarc = str(cf.get("dmarc") or f"v=DMARC1; p=quarantine; rua=mailto:postmaster@{selected_domain}; adkim=s; aspf=s")
    dmarc = dmarc.replace("postmaster@example.com", f"postmaster@{selected_domain}")

    ports = build_ports(selected_bind, pop3_enabled)
    dns_records = build_dns_records(
        domain=selected_domain,
        mail_host=selected_mail_host,
        target_address=selected_target,
        ttl=ttl,
        spf_record=spf,
        dmarc_record=dmarc,
    )

    warnings: list[str] = []
    if is_placeholder(selected_domain) or selected_domain == "example.com":
        warnings.append("Domain is still the example placeholder; pass --domain before real deployment.")
    if is_placeholder(selected_target):
        warnings.append("Target address is still SERVER_IP; pass --target-address or --single-host before real deployment.")
    if selected_bind == "127.0.0.1":
        warnings.append("Ports are bound to 127.0.0.1; external mail delivery and mail clients will not reach this host.")
    if selected_bind in {"0.0.0.0", "::"}:
        warnings.append("Mail ports bind publicly. Keep host firewall rules intentional and fail2ban enabled.")
    if str(docker_image or seed.get("docker_image") or DEFAULT_DMS_IMAGE).endswith(":latest"):
        warnings.append("Docker Mailserver image uses ':latest'. Pin a tested tag before long-lived production use.")
    if pop3_enabled:
        warnings.append("POP3 is enabled because requested; prefer POP3S/995 for clients and disable POP3 if nobody needs it.")
    warnings.append("Cloudflare mail A record must be DNS only, not proxied.")
    warnings.append("PTR/rDNS must be set at the VPS/IP provider, not in Cloudflare.")
    warnings.append("Outbound port 25 must be allowed by the server provider, or configure an authenticated SMTP relay.")

    service = safe_id(service_name if service_name is not None else str(seed.get("service_name") or "main-computer-mail"), kind="service name")
    project = safe_id(compose_project if compose_project is not None else str(seed.get("compose_project") or service), kind="compose project")

    return MailServerPlan(
        name=name,
        description=str(seed.get("description") or ""),
        domain=selected_domain,
        mail_host=selected_mail_host,
        target_address=selected_target,
        compose_project=project,
        service_name=service,
        docker_image=str(docker_image if docker_image is not None else seed.get("docker_image") or DEFAULT_DMS_IMAGE).strip(),
        certbot_image=str(certbot_image if certbot_image is not None else seed.get("certbot_image") or DEFAULT_CERTBOT_IMAGE).strip(),
        bind_host=selected_bind,
        enable_pop3=pop3_enabled,
        enable_clamav=bool(seed.get("enable_clamav", True)),
        enable_rspamd=bool(seed.get("enable_rspamd", True)),
        enable_fail2ban=bool(seed.get("enable_fail2ban", True)),
        tls_mode=selected_tls_mode,
        postmaster=selected_postmaster,
        admin_mailbox=selected_admin,
        mailbox_quota=str(seed.get("mailbox_quota") or "0"),
        message_size_limit=str(seed.get("message_size_limit") or "52428800"),
        rspamd_greylisting=bool(seed.get("rspamd_greylisting", True)),
        rspamd_learn=bool(seed.get("rspamd_learn", True)),
        ipv4_only=bool(seed.get("ipv4_only", True)),
        cloudflare_ttl=ttl,
        spf_record=spf,
        dmarc_record=dmarc,
        ports=ports,
        dns_records=dns_records,
        warnings=tuple(warnings),
    )


def yaml_quote(value: object) -> str:
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def env_pairs(plan: MailServerPlan) -> list[tuple[str, str]]:
    return [
        ("OVERRIDE_HOSTNAME", plan.mail_host),
        ("POSTMASTER_ADDRESS", plan.postmaster),
        ("LOG_LEVEL", "info"),
        ("SUPERVISOR_LOGLEVEL", "warn"),
        ("SSL_TYPE", "letsencrypt" if plan.tls_mode in {"host-letsencrypt", "cloudflare-dns01"} else "manual"),
        ("SSL_DOMAIN", plan.mail_host),
        ("ENABLE_IMAP", "1"),
        ("ENABLE_POP3", "1" if plan.enable_pop3 else "0"),
        ("ENABLE_CLAMAV", "1" if plan.enable_clamav else "0"),
        ("ENABLE_FAIL2BAN", "1" if plan.enable_fail2ban else "0"),
        ("ENABLE_RSPAMD", "1" if plan.enable_rspamd else "0"),
        ("ENABLE_RSPAMD_REDIS", "1" if plan.enable_rspamd else "0"),
        ("ENABLE_AMAVIS", "0"),
        ("ENABLE_SPAMASSASSIN", "0"),
        ("ENABLE_OPENDKIM", "0" if plan.enable_rspamd else "1"),
        ("ENABLE_OPENDMARC", "0" if plan.enable_rspamd else "1"),
        ("ENABLE_POLICYD_SPF", "0" if plan.enable_rspamd else "1"),
        ("RSPAMD_GREYLISTING", "1" if plan.rspamd_greylisting else "0"),
        ("RSPAMD_LEARN", "1" if plan.rspamd_learn else "0"),
        ("RSPAMD_CHECK_AUTHENTICATED", "0"),
        ("SPOOF_PROTECTION", "1"),
        ("MOVE_SPAM_TO_JUNK", "1"),
        ("MARK_SPAM_AS_READ", "0"),
        ("PERMIT_DOCKER", "none"),
        ("POSTFIX_INET_PROTOCOLS", "ipv4" if plan.ipv4_only else "all"),
        ("DOVECOT_INET_PROTOCOLS", "ipv4" if plan.ipv4_only else "all"),
        ("POSTFIX_MESSAGE_SIZE_LIMIT", plan.message_size_limit),
        ("POSTFIX_MAILBOX_SIZE_LIMIT", plan.mailbox_quota),
        ("ENABLE_UPDATE_CHECK", "0"),
        ("DMS_CONFIG_POLL", "2"),
    ]


def render_env(plan: MailServerPlan) -> str:
    lines = [
        "# Generated by tools/coolify_mail_server.py",
        "# Kept for manual review/write mode; the Coolify API compose payload also embeds these values inline.",
    ]
    lines.extend(f"{key}={value}" for key, value in env_pairs(plan))
    return "\n".join(lines) + "\n"


def volume_name(plan: MailServerPlan, suffix: str) -> str:
    return safe_id(f"{plan.service_name}-{suffix}", kind="volume")


def render_ports(plan: MailServerPlan) -> list[str]:
    return [f'{port.bind_host}:{port.host_port}:{port.container_port}' for port in plan.ports if port.enabled]


def render_compose(plan: MailServerPlan) -> str:
    data_volume = volume_name(plan, "data")
    state_volume = volume_name(plan, "state")
    logs_volume = volume_name(plan, "logs")
    config_volume = volume_name(plan, "config")
    certs_volume = volume_name(plan, "certs")
    certbot_logs_volume = volume_name(plan, "certbot-logs")

    lines: list[str] = [
        f"name: {plan.compose_project}",
        "",
        "services:",
        "  mailserver:",
        f"    image: {yaml_quote(plan.docker_image)}",
        f"    container_name: {yaml_quote(plan.service_name)}",
        f"    hostname: {yaml_quote(plan.mail_host)}",
        "    environment:",
    ]
    for key, value in env_pairs(plan):
        lines.append(f"      {key}: {yaml_quote(value)}")
    lines.append("    ports:")
    for port in plan.ports:
        if port.enabled:
            lines.append(f"      - {yaml_quote(f'{port.bind_host}:{port.host_port}:{port.container_port}')}")
    lines.extend(
        [
            "    volumes:",
            f"      - {yaml_quote(data_volume + ':/var/mail/')}",
            f"      - {yaml_quote(state_volume + ':/var/mail-state/')}",
            f"      - {yaml_quote(logs_volume + ':/var/log/mail/')}",
            f"      - {yaml_quote(config_volume + ':/tmp/docker-mailserver/')}",
        ]
    )
    if plan.tls_mode == "cloudflare-dns01":
        lines.append(f"      - {yaml_quote(certs_volume + ':/etc/letsencrypt/:ro')}")
    elif plan.tls_mode == "host-letsencrypt":
        lines.append('      - "/etc/letsencrypt:/etc/letsencrypt:ro"')
    else:
        lines.extend(
            [
                '      - "/srv/tls/mail:/srv/tls/mail:ro"',
                "    # For tls_mode=manual, set SSL_CERT_PATH and SSL_KEY_PATH in mailserver.env before deploying.",
            ]
        )
    lines.extend(
        [
            '      - "/etc/localtime:/etc/localtime:ro"',
            "    restart: unless-stopped",
            "    stop_grace_period: 1m",
            "    cap_add:",
            "      - NET_ADMIN",
            "    healthcheck:",
            "      test: [\"CMD-SHELL\", \"ss -ltn | grep -q ':25 ' && ss -ltn | grep -q ':993 '\"]",
            "      interval: 30s",
            "      timeout: 5s",
            "      retries: 5",
        ]
    )

    if plan.tls_mode == "cloudflare-dns01":
        lines.extend(
            [
                "",
                "  certbot-cloudflare:",
                f"    image: {yaml_quote(plan.certbot_image)}",
                "    profiles:",
                "      - certbot",
                "    command:",
                "      - certonly",
                "      - --non-interactive",
                "      - --agree-tos",
                "      - --dns-cloudflare",
                "      - --dns-cloudflare-credentials",
                "      - /run/secrets/cloudflare.ini",
                "      - --email",
                f"      - {yaml_quote(plan.postmaster)}",
                "      - -d",
                f"      - {yaml_quote(plan.mail_host)}",
                "    volumes:",
                f"      - {yaml_quote(certs_volume + ':/etc/letsencrypt/')}",
                f"      - {yaml_quote(certbot_logs_volume + ':/var/log/letsencrypt/')}",
                '      - "./secrets/cloudflare.ini:/run/secrets/cloudflare.ini:ro"',
            ]
        )

    lines.extend(
        [
            "",
            "volumes:",
            f"  {data_volume}:",
            f"  {state_volume}:",
            f"  {logs_volume}:",
            f"  {config_volume}:",
        ]
    )
    if plan.tls_mode == "cloudflare-dns01":
        lines.extend(
            [
                f"  {certs_volume}:",
                f"  {certbot_logs_volume}:",
            ]
        )
    return "\n".join(lines) + "\n"


def render_cloudflare_records(plan: MailServerPlan) -> str:
    records: list[dict[str, Any]] = []
    for record in plan.dns_records:
        item: dict[str, Any] = {
            "type": record.type,
            "name": record.name,
            "content": record.content,
            "ttl": record.ttl,
        }
        if record.priority is not None:
            item["priority"] = record.priority
        if record.proxied is not None:
            item["proxied"] = record.proxied
        if record.comment:
            item["comment"] = record.comment
        records.append(item)
    return json.dumps({"zone": plan.domain, "records": records}, indent=2, sort_keys=True) + "\n"


def render_dns_markdown(plan: MailServerPlan) -> str:
    header = "| Type | Name | Content | Priority | Proxy |\n|---|---|---|---:|---|\n"
    rows = []
    for record in plan.dns_records:
        rows.append(
            "| {type} | {name} | `{content}` | {priority} | {proxy} |".format(
                type=record.type,
                name=record.name,
                content=record.content,
                priority="" if record.priority is None else str(record.priority),
                proxy="" if record.proxied is None else ("DNS only" if record.proxied is False else "Proxied"),
            )
        )
    return header + "\n".join(rows) + "\n"


def render_verify_script(plan: MailServerPlan) -> str:
    ports = " ".join(str(port.host_port) for port in plan.ports if port.enabled)
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        DOMAIN={shlex.quote(plan.domain)}
        MAIL_HOST={shlex.quote(plan.mail_host)}
        SERVER_IP={shlex.quote(plan.target_address)}

        echo "== DNS =="
        dig +short A "$MAIL_HOST"
        dig +short MX "$DOMAIN"
        dig +short TXT "$DOMAIN"
        dig +short TXT "_dmarc.$DOMAIN"
        dig +short TXT "mail._domainkey.$DOMAIN" || true

        echo "== reverse DNS / PTR =="
        if [ "$SERVER_IP" != "SERVER_IP" ]; then
          dig +short -x "$SERVER_IP" || true
        else
          echo "SERVER_IP placeholder is still set"
        fi

        echo "== local listening ports =="
        ss -ltnp | grep -E ':(25|465|587|143|993|110|995)\\b' || true

        echo "== remote port probes =="
        for port in {ports}; do
          timeout 5 bash -lc "cat < /dev/null > /dev/tcp/$MAIL_HOST/$port" \
            && echo "ok $MAIL_HOST:$port" \
            || echo "failed $MAIL_HOST:$port"
        done

        echo "== TLS banners =="
        openssl s_client -connect "$MAIL_HOST:993" -servername "$MAIL_HOST" -brief < /dev/null || true
        openssl s_client -connect "$MAIL_HOST:995" -servername "$MAIL_HOST" -brief < /dev/null || true
        openssl s_client -starttls smtp -connect "$MAIL_HOST:587" -servername "$MAIL_HOST" -brief < /dev/null || true
        """
    )


def render_commands(plan: MailServerPlan) -> str:
    base = f"docker exec -it {shlex.quote(plan.service_name)}"
    commands = [
        f"# Create first mailbox (replace CHANGE_ME with a strong password):",
        f"{base} setup email add {shlex.quote(plan.admin_mailbox)} 'CHANGE_ME_STRONG_PASSWORD'",
        "",
        "# Route postmaster to the admin mailbox:",
        f"{base} setup alias add {shlex.quote(plan.postmaster)} {shlex.quote(plan.admin_mailbox)}",
        "",
        "# Generate DKIM keys and then add the printed mail._domainkey TXT record in Cloudflare:",
        f"{base} setup config dkim domain {shlex.quote(plan.domain)}",
        f"{base} cat /tmp/docker-mailserver/rspamd/dkim/{shlex.quote(plan.domain)}.mail.txt || true",
        f"{base} cat /tmp/docker-mailserver/opendkim/keys/{shlex.quote(plan.domain)}/mail.txt || true",
        "",
        "# Restart after DKIM generation:",
        f"docker restart {shlex.quote(plan.service_name)}",
        "",
        "# Verify accounts:",
        f"{base} setup email list",
    ]
    if plan.tls_mode == "cloudflare-dns01":
        commands.insert(
            0,
            "# Before starting mailserver for the first time, create ./secrets/cloudflare.ini in the Coolify service workdir with:\n"
            "# dns_cloudflare_api_token = <token with Zone:DNS:Edit for this zone only>\n"
            "# Then provision the certificate with:\n"
            "docker compose --profile certbot run --rm certbot-cloudflare\n",
        )
    return "\n".join(commands) + "\n"


def render_readme(plan: MailServerPlan) -> str:
    return textwrap.dedent(
        f"""\
        # Coolify mail server deployment

        Generated for `{plan.domain}` with mail host `{plan.mail_host}`.

        ## Files

        - `compose.yaml` — raw Docker Compose for a Coolify service.
        - `mailserver.env` — Docker Mailserver environment.
        - `cloudflare-records.json` — DNS records to upsert in Cloudflare.
        - `verify-mail-server.sh` — DNS, PTR, port, and TLS probes.
        - `operator-commands.txt` — mailbox, alias, and DKIM bootstrap commands.

        ## Coolify notes

        This stack publishes SMTP/IMAP/POP3 ports directly with Docker Compose
        `ports:` mappings. Do not put `mail.{plan.domain}` behind the Coolify
        HTTP proxy or the Cloudflare orange-cloud proxy.

        ## DNS

        {render_dns_markdown(plan)}
        ## State that is outside this deployer

        - PTR/rDNS for `{plan.target_address}` must be set at the VPS/IP provider
          to `{plan.mail_host}`.
        - Outbound port 25 must be allowed by the provider, or you need an SMTP relay.
        - DKIM TXT content is generated after the first mailbox exists.

        ## First-run sequence

        1. Apply the Coolify service.
        2. Confirm DNS and PTR.
        3. Create the first mailbox and postmaster alias from `operator-commands.txt`.
        4. Generate DKIM and add the printed TXT record in Cloudflare.
        5. Restart the mailserver container.
        6. Run `./verify-mail-server.sh`.

        """
    )


def write_outputs(plan: MailServerPlan, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "compose.yaml": render_compose(plan),
        "mailserver.env": render_env(plan),
        "cloudflare-records.json": render_cloudflare_records(plan),
        "verify-mail-server.sh": render_verify_script(plan),
        "operator-commands.txt": render_commands(plan),
        "README.md": render_readme(plan),
        "plan.json": json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n",
    }
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        if name.endswith(".sh"):
            path.chmod(0o755)


def base64_compose(compose: str) -> str:
    return base64.b64encode(compose.encode("utf-8")).decode("ascii")


def resolve_token(args: argparse.Namespace) -> tuple[str, str]:
    explicit = str(getattr(args, "coolify_token", "") or "").strip()
    if explicit:
        return explicit, "--coolify-token"
    env_name = str(getattr(args, "coolify_token_env", "") or DEFAULT_COOLIFY_TOKEN_ENV).strip()
    if env_name:
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip(), f"env:{env_name}"
    token_file = str(getattr(args, "coolify_token_file", "") or "").strip()
    if token_file:
        value = Path(token_file).read_text(encoding="utf-8").strip()
        if value:
            return value, f"file:{token_file}"
    return "", "missing"


def coolify_client_from_args(args: argparse.Namespace) -> tuple[CoolifyClient, str, str]:
    token, token_source = resolve_token(args)
    if not token:
        raise CoolifyMailError(
            f"Missing Coolify token. Set {getattr(args, 'coolify_token_env', DEFAULT_COOLIFY_TOKEN_ENV)} "
            "or pass --coolify-token-file."
        )
    base_url = str(getattr(args, "coolify_url", "") or "").strip()
    if not base_url:
        raise CoolifyMailError("Missing --coolify-url.")
    return (
        CoolifyClient(
            base_url,
            token,
            timeout_s=float(getattr(args, "coolify_timeout_s", DEFAULT_COOLIFY_API_TIMEOUT_S)),
            retries=int(getattr(args, "coolify_retries", DEFAULT_COOLIFY_API_RETRIES)),
            retry_sleep_s=float(getattr(args, "coolify_retry_sleep_s", DEFAULT_COOLIFY_API_RETRY_SLEEP_S)),
        ),
        token,
        token_source,
    )


def choose_coolify_uuid(
    *,
    explicit_uuid: str,
    explicit_name: str,
    items: list[dict[str, Any]],
    kind: str,
) -> tuple[str, dict[str, Any]]:
    clean_uuid = str(explicit_uuid or "").strip()
    if clean_uuid:
        match = next((item for item in items if coolify_item_uuid(item) == clean_uuid), None)
        return clean_uuid, {
            "source": "explicit_uuid",
            "kind": kind,
            "uuid": clean_uuid,
            **({"name": coolify_item_name(match)} if match else {}),
        }
    clean_name = str(explicit_name or "").strip()
    if clean_name:
        matches = [item for item in items if coolify_item_name(item).lower() == clean_name.lower()]
        if len(matches) == 1:
            uuid = coolify_item_uuid(matches[0])
            return uuid, {"source": "exact_name", "kind": kind, "uuid": uuid, "name": coolify_item_name(matches[0])}
        if len(matches) > 1:
            return "", {
                "source": "ambiguous_name",
                "kind": kind,
                "name": clean_name,
                "matches": [coolify_item_summary(item) for item in matches],
            }
        return "", {"source": "missing_name", "kind": kind, "name": clean_name}
    if len(items) == 1:
        uuid = coolify_item_uuid(items[0])
        return uuid, {"source": "single_available", "kind": kind, "uuid": uuid, "name": coolify_item_name(items[0])}
    return "", {"source": "not_selected", "kind": kind, "count": len(items), "items": [coolify_item_summary(item) for item in items]}


def coolify_service_matches_name(item: dict[str, Any], service_name: str) -> bool:
    clean_name = str(service_name or "").strip().lower()
    if not clean_name:
        return False
    candidates = [
        str(item.get(key) or "").strip().lower()
        for key in ("name", "description", "fqdn")
        if str(item.get(key) or "").strip()
    ]
    item_name = coolify_item_name(item).strip().lower()
    if item_name:
        candidates.append(item_name)
    return any(candidate == clean_name or candidate.startswith(f"{clean_name} ") or candidate.startswith(f"{clean_name}(") for candidate in candidates)


def coolify_find_service_by_name(
    *,
    client: CoolifyClient,
    args: argparse.Namespace,
    service_name: str,
    tried: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    response, services = coolify_list(client, args, "/api/v1/services", label="services", preferred_keys=("services",))
    tried.append({"operation": "list-services-for-existing-service", "response": response_to_dict(response), "count": len(services)})
    context: dict[str, Any] = {"service_name": service_name, "list_response": response_to_dict(response), "count": len(services)}
    if not response.ok:
        context["selection"] = {"source": "api_error", "response": response_to_dict(response)}
        return "", context
    matches = [item for item in services if coolify_service_matches_name(item, service_name)]
    context["matches"] = [coolify_item_summary(item) for item in matches]
    if len(matches) == 1:
        uuid = coolify_item_uuid(matches[0])
        context["selection"] = {"source": "exact_service_name", "kind": "service", "uuid": uuid, "name": coolify_item_name(matches[0])}
        return uuid, context
    if len(matches) > 1:
        context["selection"] = {
            "source": "duplicate_service_name",
            "kind": "service",
            "name": service_name,
            "matches": [coolify_item_summary(item) for item in matches],
            "message": f"Expected at most one Coolify service named {service_name!r}; found {len(matches)}.",
        }
        return "", context
    context["selection"] = {"source": "missing", "kind": "service", "name": service_name}
    return "", context


def ensure_coolify_environment(
    *,
    client: CoolifyClient,
    args: argparse.Namespace,
    project_uuid: str,
    environment_name: str,
    explicit_environment_uuid: str,
    tried: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    clean_project_uuid = str(project_uuid or "").strip()
    clean_environment_name = str(environment_name or "").strip() or "mail"
    clean_environment_uuid = str(explicit_environment_uuid or "").strip()
    if not clean_project_uuid:
        return "", {"source": "missing-project", "kind": "environment", "name": clean_environment_name}

    env_path = f"/api/v1/projects/{urllib.parse.quote(clean_project_uuid)}/environments"
    response, environments = coolify_list(client, args, env_path, label="environments", preferred_keys=("environments",))
    tried.append({"operation": "list-environments", "response": response_to_dict(response), "count": len(environments)})
    context: dict[str, Any] = {"environment_name": clean_environment_name, "environment_uuid": clean_environment_uuid}
    if not response.ok:
        context["selection"] = {"source": "api_error", "response": response_to_dict(response)}
        return clean_environment_uuid, context

    selected_uuid, selection = choose_coolify_uuid(
        explicit_uuid=clean_environment_uuid,
        explicit_name=clean_environment_name,
        items=environments,
        kind="environment",
    )
    context["selection"] = selection
    if selected_uuid:
        context["environment_uuid"] = selected_uuid
        return selected_uuid, context

    if bool(getattr(args, "no_coolify_create_environment", False)):
        context["create_skipped"] = True
        return "", context

    create_response = client.request("POST", env_path, {"name": clean_environment_name})
    tried.append({"operation": "create-environment", "path": env_path, "payload_keys": ["name"], "response": response_to_dict(create_response)})
    context["create_response"] = response_to_dict(create_response)
    if create_response.ok:
        selected_uuid = coolify_service_uuid_from_body(create_response.body)
        if selected_uuid:
            context["environment_uuid"] = selected_uuid
            context["selection"] = {"source": "created", "kind": "environment", "uuid": selected_uuid, "name": clean_environment_name}
            return selected_uuid, context
    return "", context


def resolve_coolify_create_context(
    *,
    plan: MailServerPlan,
    args: argparse.Namespace,
    client: CoolifyClient,
    tried: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, Any]]:
    project_uuid = str(getattr(args, "coolify_project_uuid", "") or "").strip()
    server_uuid = str(getattr(args, "coolify_server_uuid", "") or "").strip()
    destination_uuid = str(getattr(args, "coolify_destination_uuid", "") or "").strip()
    environment_name = str(getattr(args, "coolify_environment", "") or "mail").strip() or "mail"
    environment_uuid = str(getattr(args, "coolify_environment_uuid", "") or "").strip()
    context: dict[str, Any] = {
        "project_uuid": project_uuid,
        "server_uuid": server_uuid,
        "destination_uuid": destination_uuid,
        "environment_name": environment_name,
        "environment_uuid": environment_uuid,
    }

    if not project_uuid:
        response, projects = coolify_list(client, args, "/api/v1/projects", label="projects", preferred_keys=("projects",))
        tried.append({"operation": "list-projects", "response": response_to_dict(response), "count": len(projects)})
        if response.ok:
            project_uuid, selection = choose_coolify_uuid(
                explicit_uuid="",
                explicit_name=str(getattr(args, "coolify_project_name", "") or ""),
                items=projects,
                kind="project",
            )
            context["project_selection"] = selection
            context["project_uuid"] = project_uuid
        else:
            context["project_selection"] = {"source": "api_error", "response": response_to_dict(response)}

    if not server_uuid:
        response, servers = coolify_list(client, args, "/api/v1/servers", label="servers", preferred_keys=("servers",))
        tried.append({"operation": "list-servers", "response": response_to_dict(response), "count": len(servers)})
        if response.ok:
            server_uuid, selection = choose_coolify_uuid(
                explicit_uuid="",
                explicit_name=str(getattr(args, "coolify_server_name", "") or ""),
                items=servers,
                kind="server",
            )
            context["server_selection"] = selection
            context["server_uuid"] = server_uuid
        else:
            context["server_selection"] = {"source": "api_error", "response": response_to_dict(response)}

    if project_uuid:
        environment_uuid, environment_context = ensure_coolify_environment(
            client=client,
            args=args,
            project_uuid=project_uuid,
            environment_name=environment_name,
            explicit_environment_uuid=environment_uuid,
            tried=tried,
        )
        context["environment"] = environment_context
        context["environment_uuid"] = str(environment_uuid or "").strip()

    payload = {
        "project_uuid": str(project_uuid or "").strip(),
        "server_uuid": str(server_uuid or "").strip(),
        "destination_uuid": str(destination_uuid or "").strip(),
        "environment_name": environment_name,
        "environment_uuid": str(context.get("environment_uuid") or "").strip(),
    }
    return payload, context


def coolify_service_uuid_from_body(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("uuid", "service_uuid", "id"):
            value = str(body.get(key) or "").strip()
            if value:
                return value
        service = body.get("service")
        if isinstance(service, dict):
            return coolify_service_uuid_from_body(service)
    return ""


def coolify_check(args: argparse.Namespace) -> dict[str, Any]:
    client, token, token_source = coolify_client_from_args(args)
    response = client.request("GET", "/api/v1/version")
    return {
        "ok": response.ok,
        "url": client.base_url,
        "token": redact_secret(token),
        "token_source": token_source,
        "response": response_to_dict(response),
    }


def coolify_discover(args: argparse.Namespace) -> dict[str, Any]:
    client, token, token_source = coolify_client_from_args(args)
    result: dict[str, Any] = {
        "ok": False,
        "url": client.base_url,
        "token": redact_secret(token),
        "token_source": token_source,
    }
    version = client.request("GET", "/api/v1/version")
    result["version"] = response_to_dict(version)
    if not version.ok:
        result["stage"] = "version"
        return result
    projects_response, projects = coolify_list(client, args, "/api/v1/projects", label="projects", preferred_keys=("projects",))
    servers_response, servers = coolify_list(client, args, "/api/v1/servers", label="servers", preferred_keys=("servers",))
    services_response, services = coolify_list(client, args, "/api/v1/services", label="services", preferred_keys=("services",))
    environments_response: CoolifyResponse | None = None
    environments: list[dict[str, Any]] = []
    project_uuid, project_selection = ("", {"source": "api_error"})
    if projects_response.ok:
        project_uuid, project_selection = choose_coolify_uuid(
            explicit_uuid=str(getattr(args, "coolify_project_uuid", "") or ""),
            explicit_name=str(getattr(args, "coolify_project_name", "") or ""),
            items=projects,
            kind="project",
        )
    if project_uuid:
        environments_response, environments = coolify_list(
            client,
            args,
            f"/api/v1/projects/{urllib.parse.quote(project_uuid)}/environments",
            label="environments",
            preferred_keys=("environments",),
        )
    result.update(
        {
            "ok": bool(projects_response.ok and servers_response.ok),
            "projects": [coolify_item_summary(item) for item in projects],
            "project_selection": project_selection,
            "servers": [coolify_item_summary(item) for item in servers],
            "services": [coolify_item_summary(item) for item in services],
            "environments": [coolify_item_summary(item) for item in environments],
            "responses": {
                "projects": response_to_dict(projects_response),
                "servers": response_to_dict(servers_response),
                "services": response_to_dict(services_response),
                **({"environments": response_to_dict(environments_response)} if environments_response is not None else {}),
            },
        }
    )
    return result


def coolify_sync(plan: MailServerPlan, args: argparse.Namespace, *, deploy: bool = False) -> dict[str, Any]:
    compose = render_compose(plan)
    compose_b64 = base64_compose(compose)
    service_name = str(getattr(args, "coolify_service_name", "") or plan.service_name)
    service_uuid = str(getattr(args, "coolify_service_uuid", "") or "").strip()

    if bool(getattr(args, "dry_run", False)):
        return {
            "ok": True,
            "dry_run": True,
            "action": "coolify-sync",
            "service_name": service_name,
            "service_uuid": service_uuid,
            "compose": compose,
            "mailserver_env": render_env(plan),
            "compose_base64_bytes": len(compose_b64),
        }

    client, token, token_source = coolify_client_from_args(args)
    version = client.request("GET", "/api/v1/version")
    if not version.ok:
        return {"ok": False, "stage": "version", "response": response_to_dict(version, token=token, token_source=token_source)}

    tried: list[dict[str, Any]] = []
    create_context: dict[str, Any] = {}
    existing_service_context: dict[str, Any] = {}
    if not service_uuid:
        existing_service_uuid, existing_service_context = coolify_find_service_by_name(
            client=client,
            args=args,
            service_name=service_name,
            tried=tried,
        )
        if existing_service_uuid:
            service_uuid = existing_service_uuid
        else:
            selection = existing_service_context.get("selection") if isinstance(existing_service_context, dict) else {}
            source = selection.get("source") if isinstance(selection, dict) else ""
            if source == "duplicate_service_name":
                return {
                    "ok": False,
                    "stage": "duplicate-service-name",
                    "service_name": service_name,
                    "message": (
                        f"Multiple Coolify services named {service_name!r} already exist. "
                        "Refusing to create or guess. Pass --coolify-service-uuid for the exact service."
                    ),
                    "context": {"existing_service": existing_service_context},
                    "tried": tried,
                }
            if source == "api_error":
                return {
                    "ok": False,
                    "stage": "service-discovery",
                    "service_name": service_name,
                    "message": "Could not list existing Coolify services before creation. Refusing to create blindly.",
                    "context": {"existing_service": existing_service_context},
                    "tried": tried,
                }

    if not service_uuid:
        create_refs, create_context = resolve_coolify_create_context(plan=plan, args=args, client=client, tried=tried)
        create_context["existing_service"] = existing_service_context
        project_uuid = str(create_refs.get("project_uuid") or "").strip()
        server_uuid = str(create_refs.get("server_uuid") or "").strip()
        environment_uuid = str(create_refs.get("environment_uuid") or "").strip()
        if not project_uuid or not server_uuid or not environment_uuid:
            return {
                "ok": False,
                "stage": "missing-create-context",
                "message": "Coolify service creation requires project_uuid, server_uuid, and environment_uuid.",
                "context": create_context,
                "tried": tried,
            }

        create_payload = {
            "server_uuid": server_uuid,
            "project_uuid": project_uuid,
            "environment_name": str(create_refs.get("environment_name") or "mail"),
            "environment_uuid": environment_uuid,
            "name": service_name,
            "description": f"Main Computer mail server for {plan.domain} generated by tools/coolify_mail_server.py",
            "docker_compose_raw": compose_b64,
            "instant_deploy": False,
        }
        destination_uuid = str(create_refs.get("destination_uuid") or "").strip()
        if destination_uuid:
            create_payload["destination_uuid"] = destination_uuid
        create_response = client.request("POST", "/api/v1/services", create_payload)
        tried.append(
            {
                "operation": "create-service",
                "path": "/api/v1/services",
                "payload_keys": sorted(create_payload.keys()),
                "docker_compose_raw_encoding": "base64",
                "response": response_to_dict(create_response),
            }
        )
        service_uuid = coolify_service_uuid_from_body(create_response.body)
        if not create_response.ok or not service_uuid:
            return {"ok": False, "stage": "create-service", "service_uuid": service_uuid, "context": create_context, "tried": tried}

    update_payloads = [
        {"docker_compose_raw": compose_b64, "name": service_name},
        {"docker_compose_raw": compose_b64},
        {"docker_compose": compose, "name": service_name},
        {"compose": compose, "name": service_name},
    ]
    update_paths = [f"/api/v1/services/{service_uuid}", f"/api/v1/services/{service_uuid}/compose"]
    update_ok = False
    for path in update_paths:
        for payload in update_payloads:
            response = client.request("PATCH", path, payload)
            tried.append(
                {
                    "operation": "update-service",
                    "path": path,
                    "payload_keys": sorted(payload.keys()),
                    "docker_compose_raw_encoding": "base64" if "docker_compose_raw" in payload else "plain",
                    "response": response_to_dict(response),
                }
            )
            if response.ok:
                update_ok = True
                break
            if response.status == 405:
                response = client.request("PUT", path, payload)
                tried.append(
                    {
                        "operation": "update-service-put",
                        "path": path,
                        "payload_keys": sorted(payload.keys()),
                        "response": response_to_dict(response),
                    }
                )
                if response.ok:
                    update_ok = True
                    break
        if update_ok:
            break

    result_context = create_context or ({"existing_service": existing_service_context} if existing_service_context else {})
    if not update_ok:
        return {"ok": False, "stage": "update-service", "service_uuid": service_uuid, "context": result_context, "tried": tried}

    deploy_result: dict[str, Any] | None = None
    if deploy:
        deploy_paths = [
            f"/api/v1/deploy?uuid={urllib.parse.quote(service_uuid)}&force=true",
            f"/api/v1/services/{service_uuid}/start",
            f"/api/v1/services/{service_uuid}/restart",
            f"/api/v1/services/{service_uuid}/deploy",
        ]
        for path in deploy_paths:
            method = "GET" if path.startswith("/api/v1/deploy?") else "POST"
            response = client.request(method, path)
            tried.append({"operation": "deploy", "path": path, "response": response_to_dict(response)})
            if response.ok:
                deploy_result = response_to_dict(response)
                break
        if deploy_result is None:
            return {"ok": False, "stage": "deploy-service", "service_uuid": service_uuid, "context": result_context, "tried": tried}

    return {
        "ok": True,
        "service_uuid": service_uuid,
        "service_name": service_name,
        "deployed": bool(deploy_result),
        "deploy_requested": bool(deploy_result),
        "deploy_result": deploy_result,
        "context": result_context,
        "tried": tried,
    }


def apply_mail_server(plan: MailServerPlan, args: argparse.Namespace) -> dict[str, Any]:
    phases: list[dict[str, Any]] = []
    if bool(getattr(args, "dry_run", False)):
        phases.append({"phase": "coolify-check", "result": {"ok": True, "dry_run": True, "url": str(getattr(args, "coolify_url", "") or "")}})
    else:
        check_result = coolify_check(args)
        phases.append({"phase": "coolify-check", "result": check_result})
        if not check_result.get("ok"):
            return {"ok": False, "mail_host": plan.mail_host, "phases": phases}
    sync_result = coolify_sync(plan, args, deploy=not bool(getattr(args, "no_deploy", False)))
    phases.append({"phase": "coolify-sync", "result": sync_result})
    if not sync_result.get("ok"):
        return {"ok": False, "mail_host": plan.mail_host, "phases": phases}
    return {
        "ok": True,
        "mail_host": plan.mail_host,
        "domain": plan.domain,
        "phases": phases,
        "next_steps": [
            "Apply Cloudflare DNS records from the dns action / cloudflare-records.json.",
            "Set PTR/rDNS at the VPS provider.",
            "Create first mailbox and DKIM with the commands action.",
            "Run verify after DNS and TLS are ready.",
        ],
    }


def validate_for_apply(plan: MailServerPlan) -> None:
    if plan.domain == "example.com" or plan.mail_host == "mail.example.com":
        raise PlanError("Refusing to apply placeholder example.com. Pass --domain and --target-address.")
    if is_placeholder(plan.target_address):
        raise PlanError("Refusing to apply placeholder SERVER_IP. Pass --target-address or --single-host.")
    host_ports = [port.host_port for port in plan.ports if port.enabled]
    if len(host_ports) != len(set(host_ports)):
        raise PlanError("Duplicate host port in mail server plan.")
    if not any(record.type == "MX" and record.content == plan.mail_host for record in plan.dns_records):
        raise PlanError("Plan does not contain an MX record pointing at the mail host.")


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def render_operator_runbook() -> str:
    return textwrap.dedent(
        r"""
        Main Computer Coolify mail server runbook
        ========================================

        This tool renders and can push a Docker Mailserver raw Compose service
        into Coolify. It is intentionally mail-protocol aware: SMTP, IMAP, and
        POP3 are exposed with Docker Compose `ports:` mappings on the host,
        outside Coolify's HTTP reverse proxy.

        1. Render a plan

            python .\tools\coolify_mail_server.py plan production `
              --domain example.com `
              --target-address 203.0.113.10

        2. Render files for review

            python .\tools\coolify_mail_server.py write production `
              --domain example.com `
              --target-address 203.0.113.10 `
              --out runtime\coolify-mail\example.com

        3. Set Cloudflare DNS

            python .\tools\coolify_mail_server.py dns production `
              --domain example.com `
              --target-address 203.0.113.10

        Make the A record DNS only / gray-cloud. Do not proxy mail.example.com.

        4. Push the service to Coolify

            $env:MAIN_COMPUTER_COOLIFY_TOKEN = "paste-token-here"

            python .\tools\coolify_mail_server.py apply production `
              --domain example.com `
              --target-address 203.0.113.10 `
              --coolify-url http://203.0.113.10:8000 `
              --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
              --coolify-service-name main-computer-mail

        5. Bootstrap mailboxes and DKIM

            python .\tools\coolify_mail_server.py commands production `
              --domain example.com `
              --target-address 203.0.113.10

        Run the printed docker exec commands on the remote host. Add the DKIM
        TXT record that Docker Mailserver prints, then restart the container.

        6. Verify

            python .\tools\coolify_mail_server.py write production `
              --domain example.com `
              --target-address 203.0.113.10 `
              --out runtime\coolify-mail\example.com

            ssh root@203.0.113.10
            cd <Coolify service compose directory>
            ./verify-mail-server.sh

        Required provider-side state that this tool cannot set:

        - PTR/rDNS for the server IP should resolve to mail.example.com.
        - Inbound and outbound TCP/25 must be allowed, unless you configure a relay.
        - Cloudflare Email Routing should not own the root MX records for this same domain.
        """
    ).strip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan, render, and apply a Coolify-managed Docker Mailserver stack.")
    parser.add_argument(
        "action",
        nargs="?",
        default="docs",
        choices=[
            "docs",
            "help",
            "quickstart",
            "list",
            "plan",
            "compose",
            "env",
            "dns",
            "commands",
            "write",
            "validate",
            "coolify-check",
            "coolify-discover",
            "coolify-sync",
            "apply",
        ],
        help="Action to run. Omit this to print the step-by-step deployment runbook.",
    )
    parser.add_argument("seed", nargs="?", default="production", help="Seed name from MAIL_SERVER_SEEDS or a JSON seed path.")
    parser.add_argument("--domain", default="", help="Mail domain, e.g. example.com.")
    parser.add_argument("--mail-host", default="", help="Mail server FQDN. Defaults to mail.<domain>.")
    parser.add_argument("--target-address", default="", help="Server public IP/DNS for Cloudflare A record and verification.")
    parser.add_argument("--single-host", default="", help="SSH target/address, e.g. root@203.0.113.10. Host portion is used as target-address.")
    parser.add_argument("--bind-host", default="", help="IP to bind mail ports on the Docker host. Defaults to 0.0.0.0.")
    parser.add_argument("--no-pop3", action="store_true", help="Disable POP3/POP3S ports and DMS POP3 support.")
    parser.add_argument("--tls-mode", default="", choices=["", "host-letsencrypt", "cloudflare-dns01", "manual"], help="TLS certificate source.")
    parser.add_argument("--docker-image", default="", help="Override Docker Mailserver image.")
    parser.add_argument("--certbot-image", default="", help="Override certbot Cloudflare image.")
    parser.add_argument("--compose-project", default="", help="Override Compose project name.")
    parser.add_argument("--service-name", default="", help="Override container/service name.")
    parser.add_argument("--admin-mailbox", default="", help="Initial mailbox shown in bootstrap commands. Defaults to admin@domain.")
    parser.add_argument("--postmaster", default="", help="Postmaster address. Defaults to postmaster@domain.")
    parser.add_argument("--out", default="", help="Output directory for write action.")

    parser.add_argument("--coolify-url", default="", help="Coolify base URL, e.g. http://203.0.113.10:8000.")
    parser.add_argument("--coolify-token", default="", help="Coolify bearer token. Prefer --coolify-token-env when possible.")
    parser.add_argument("--coolify-token-env", default=DEFAULT_COOLIFY_TOKEN_ENV, help="Environment variable containing the Coolify token.")
    parser.add_argument("--coolify-token-file", default="", help="File containing the Coolify token.")
    parser.add_argument("--coolify-timeout-s", type=float, default=DEFAULT_COOLIFY_API_TIMEOUT_S)
    parser.add_argument("--coolify-retries", type=int, default=DEFAULT_COOLIFY_API_RETRIES)
    parser.add_argument("--coolify-retry-sleep-s", type=float, default=DEFAULT_COOLIFY_API_RETRY_SLEEP_S)

    parser.add_argument("--coolify-service-uuid", default="", help="Existing Coolify service UUID to update/deploy.")
    parser.add_argument("--coolify-service-name", default="", help="Coolify service name to create/use.")
    parser.add_argument("--coolify-project-uuid", default="", help="Project UUID for API service creation when no service UUID exists.")
    parser.add_argument("--coolify-project-name", default="", help="Project name to auto-select when discovering project UUID.")
    parser.add_argument("--coolify-server-uuid", default="", help="Server UUID for API service creation when no service UUID exists.")
    parser.add_argument("--coolify-server-name", default="", help="Server name to auto-select when discovering server UUID.")
    parser.add_argument("--coolify-destination-uuid", default="", help="Destination UUID for API service creation when no service UUID exists.")
    parser.add_argument("--coolify-environment", default="mail", help="Environment name for API service creation. Defaults to mail.")
    parser.add_argument("--coolify-environment-uuid", default="", help="Environment UUID for API service creation when the name is ambiguous.")
    parser.add_argument("--no-coolify-create-environment", action="store_true", help="Do not create the Coolify environment if it is missing.")

    parser.add_argument("--dry-run", action="store_true", help="Render/show intended changes without mutating Coolify.")
    parser.add_argument("--no-deploy", action="store_true", help="For coolify-sync/apply, update the service but do not trigger a deployment.")
    parser.add_argument("--quiet", action="store_true", help="Suppress operator progress logs; final JSON is still printed.")
    return parser.parse_args(argv)


def build_plan_from_args(args: argparse.Namespace) -> MailServerPlan:
    target_address = args.target_address or (normalize_target_address(args.single_host) if args.single_host else None)
    return build_plan(
        args.seed,
        domain=args.domain or None,
        mail_host=args.mail_host or None,
        target_address=target_address,
        compose_project=args.compose_project or None,
        service_name=args.service_name or args.coolify_service_name or None,
        docker_image=args.docker_image or None,
        certbot_image=args.certbot_image or None,
        bind_host=args.bind_host or None,
        enable_pop3=False if args.no_pop3 else None,
        tls_mode=args.tls_mode or None,
        postmaster=args.postmaster or None,
        admin_mailbox=args.admin_mailbox or None,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.action in {"docs", "help", "quickstart"}:
        print(render_operator_runbook())
        return 0

    if args.action == "list":
        print_json({"seeds": sorted(MAIL_SERVER_SEEDS)})
        return 0

    try:
        plan = build_plan_from_args(args)
        if args.action in {"coolify-sync", "apply"}:
            validate_for_apply(plan)
        if args.action == "validate":
            validate_for_apply(plan)
            print_json({"ok": True, "mail_host": plan.mail_host, "domain": plan.domain, "warnings": list(plan.warnings)})
            return 0
        if args.action == "plan":
            print_json(plan.to_dict())
            return 0
        if args.action == "compose":
            print(render_compose(plan), end="")
            return 0
        if args.action == "env":
            print(render_env(plan), end="")
            return 0
        if args.action == "dns":
            print(render_cloudflare_records(plan), end="")
            return 0
        if args.action == "commands":
            print(render_commands(plan), end="")
            return 0
        if args.action == "write":
            out_dir = Path(args.out or Path("runtime") / "coolify-mail" / plan.domain)
            write_outputs(plan, out_dir)
            print_json({"ok": True, "out": str(out_dir), "domain": plan.domain, "mail_host": plan.mail_host})
            return 0
        if args.action == "coolify-check":
            result = coolify_check(args)
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.action == "coolify-discover":
            result = coolify_discover(args)
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.action == "coolify-sync":
            result = coolify_sync(plan, args, deploy=not bool(args.no_deploy))
            print_json(result)
            return 0 if result.get("ok") else 1
        if args.action == "apply":
            result = apply_mail_server(plan, args)
            print_json(result)
            return 0 if result.get("ok") else 1
    except (PlanError, CoolifyMailError, TimeoutError, socket.timeout) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Unsupported action: {args.action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

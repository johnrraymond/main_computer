#!/usr/bin/env python3
'''Render a Cloudflare Email Worker plus a Coolify HTTPS mail-ingest service.

This is the hidden-origin counterpart to ``tools/coolify_mail_server.py``.
Cloudflare receives SMTP for the domain through Email Routing, invokes an Email
Worker, and the Worker posts the raw RFC822 message to a proxied/tunneled HTTPS
ingest endpoint. The ingest endpoint writes each message to a Maildir-compatible
store that a separate Dovecot/webmail layer can read.

The generated Coolify compose is intentionally self-contained: it embeds the
small Python ingest app in the service command so a raw Compose resource can be
pushed without depending on extra files on the remote host. The same ingest app
is also written as ``coolify/mail_ingest.py`` for review and local testing.
'''

from __future__ import annotations

import argparse
import base64
import copy
import getpass
import hashlib
import json
import os
import re
import secrets
import sys
import textwrap
import urllib.parse
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}\.?$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$", re.IGNORECASE)
EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]{1,64}@[A-Z0-9.-]{1,253}\.[A-Z]{2,63}$", re.IGNORECASE)
DEFAULT_SECRET_NAME = "MAIL_INGEST_SECRET"
DEFAULT_INGEST_PATH = "/inbound/cloudflare-email"
DEFAULT_LISTEN_PORT = 8080
DEFAULT_MAX_MESSAGE_BYTES = 25 * 1024 * 1024
USER_REGISTRY_SCHEMA = "main-computer.great-library-mail-users.v1"
USER_REGISTRY_FILE = "mail-users.json"
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 12
RESERVED_MAIL_LOCALS = (
    "admin",
    "root",
    "postmaster",
    "abuse",
    "security",
    "info",
    "support",
    "billing",
)

TOOL_DIR = Path(__file__).resolve().parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from coolify_mail_server import (  # noqa: E402
    DEFAULT_COOLIFY_API_RETRIES,
    DEFAULT_COOLIFY_API_RETRY_SLEEP_S,
    DEFAULT_COOLIFY_API_TIMEOUT_S,
    DEFAULT_COOLIFY_TOKEN_ENV,
    CoolifyClient,
    CoolifyMailError,
    CoolifyResponse,
    base64_compose,
    choose_coolify_uuid,
    coolify_find_service_by_name,
    coolify_item_summary,
    coolify_list,
    coolify_service_uuid_from_body,
    response_to_dict,
    redact_secret,
    resolve_coolify_create_context,
)



class WorkerPlanError(ValueError):
    '''Raised when the Cloudflare mail worker plan is unsafe or incomplete.'''


@dataclass(frozen=True)
class CloudflareMailWorkerPlan:
    domain: str
    worker_name: str
    ingest_host: str
    ingest_path: str
    ingest_url: str
    secret_binding: str
    secret_header: str
    service_name: str
    compose_project: str
    listen_port: int
    mailbox_root: str
    maildir_domain: str
    max_message_bytes: int
    coolify_url: str
    catch_all: bool
    local_parts: tuple[str, ...]
    failure_policy: str
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "main-computer.cloudflare-mail-worker.v1",
            **asdict(self),
            "local_parts": list(self.local_parts),
            "warnings": list(self.warnings),
        }


def safe_id(value: str, *, kind: str = "id") -> str:
    clean = str(value or "").strip().lower()
    clean = re.sub(r"[^a-z0-9_.-]+", "-", clean)
    clean = re.sub(r"-+", "-", clean).strip("-._")
    if not clean:
        raise WorkerPlanError(f"Missing {kind}.")
    if len(clean) > 63:
        clean = clean[:63].rstrip("-._")
    if not SAFE_ID_RE.match(clean):
        raise WorkerPlanError(f"Unsafe {kind}: {value!r}")
    return clean


def normalize_domain(value: str, *, field: str = "domain") -> str:
    clean = str(value or "").strip().lower().rstrip(".")
    if not clean:
        raise WorkerPlanError(f"Missing {field}.")
    if clean == "example.com":
        raise WorkerPlanError("Refusing placeholder example.com; pass a real domain.")
    if not DOMAIN_RE.match(clean):
        raise WorkerPlanError(f"Invalid {field}: {value!r}")
    return clean


def normalize_host(value: str, *, field: str) -> str:
    clean = normalize_domain(value, field=field)
    if clean == "localhost":
        raise WorkerPlanError(f"{field} must be a public HTTPS hostname, not localhost.")
    return clean


def normalize_path(value: str) -> str:
    clean = str(value or DEFAULT_INGEST_PATH).strip()
    if not clean.startswith("/"):
        clean = "/" + clean
    parsed = urllib.parse.urlsplit(clean)
    if parsed.scheme or parsed.netloc or ".." in parsed.path.split("/"):
        raise WorkerPlanError(f"Unsafe ingest path: {value!r}")
    return parsed.path or DEFAULT_INGEST_PATH


def normalize_coolify_url(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    parsed = urllib.parse.urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WorkerPlanError(f"Coolify URL must be an http(s) URL, got {value!r}.")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def parse_local_parts(values: list[str] | tuple[str, ...] | str) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_items = [item.strip() for item in values.split(",")]
    else:
        raw_items = []
        for value in values:
            raw_items.extend(str(value or "").split(","))
    parts: list[str] = []
    for raw in raw_items:
        item = raw.strip().lower()
        if not item:
            continue
        if "@" in item:
            item = item.split("@", 1)[0]
        if item in {"*", "catch-all"}:
            continue
        if not re.match(r"^[a-z0-9][a-z0-9._+-]{0,63}$", item):
            raise WorkerPlanError(f"Unsafe local mailbox part: {raw!r}")
        if item not in parts:
            parts.append(item)
    return tuple(parts)



def normalize_email_address(value: str, *, field: str = "email address") -> str:
    clean = str(value or "").strip().lower()
    if not clean:
        raise WorkerPlanError(f"Missing {field}.")
    if clean == "example@example.com" or clean.endswith("@example.com"):
        raise WorkerPlanError(f"Refusing placeholder {field}: {value!r}")
    if not EMAIL_RE.match(clean):
        raise WorkerPlanError(f"Invalid {field}: {value!r}")
    return clean


def parse_forward_routes(
    *,
    forward_pairs: list[str] | tuple[str, ...] = (),
    forward_locals: list[str] | tuple[str, ...] = (),
    forward_tos: list[str] | tuple[str, ...] = (),
) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []

    for raw in forward_pairs or ():
        item = str(raw or "").strip()
        if not item:
            continue
        if "=" not in item:
            raise WorkerPlanError("--forward must use LOCAL=destination@example.com.")
        local, destination = item.split("=", 1)
        locals_parsed = parse_local_parts(local)
        if len(locals_parsed) != 1:
            raise WorkerPlanError(f"--forward local part is invalid: {local!r}")
        pairs.append((locals_parsed[0], normalize_email_address(destination, field="forward destination")))

    locals_flat = parse_local_parts(forward_locals or ())
    destinations = [normalize_email_address(value, field="forward destination") for value in (forward_tos or ()) if str(value or "").strip()]
    if locals_flat or destinations:
        if len(locals_flat) != len(destinations):
            raise WorkerPlanError("--forward-local and --forward-to must be supplied the same number of times.")
        pairs.extend(zip(locals_flat, destinations))

    result: dict[str, str] = {}
    for local, destination in pairs:
        existing = result.get(local)
        if existing and existing != destination:
            raise WorkerPlanError(f"Conflicting forward destinations for {local!r}: {existing!r} and {destination!r}.")
        result[local] = destination
    return tuple(sorted(result.items()))


def build_routing_contract(
    *,
    domain: str,
    worker_name: str,
    forwards: tuple[tuple[str, str], ...] = (),
    drops: tuple[str, ...] = (),
    worker_local_parts: tuple[str, ...] = (),
    catch_all_to_worker: bool = True,
) -> dict[str, Any]:
    domain = normalize_domain(domain)
    drops = tuple(sorted(parse_local_parts(drops)))
    worker_local_parts = tuple(sorted(parse_local_parts(worker_local_parts)))
    forward_map = {local: destination for local, destination in forwards}

    reserved: dict[str, str] = {}
    for local in forward_map:
        reserved[local] = "forward"
    for local in drops:
        if local in reserved:
            raise WorkerPlanError(f"{local}@{domain} cannot be both forwarded and dropped.")
        reserved[local] = "drop"
    for local in worker_local_parts:
        if local in reserved:
            raise WorkerPlanError(f"{local}@{domain} has a more specific non-worker route.")
        reserved[local] = "worker"

    if not catch_all_to_worker and not worker_local_parts:
        raise WorkerPlanError("Stage one needs --catch-all-to-worker or at least one --local-part for Worker routing.")

    return {
        "domain": domain,
        "forwards": {local: forward_map[local] for local in sorted(forward_map)},
        "drops": list(drops),
        "worker_local_parts": list(worker_local_parts),
        "catch_all_to_worker": bool(catch_all_to_worker),
        "worker_name": worker_name,
    }


def generate_mail_ingest_secret() -> str:
    return secrets.token_urlsafe(48)


def resolve_prepare_secret(
    *,
    out_dir: Path,
    explicit_secret: str = "",
    secret_env: str = "",
    secret_file: str = "",
    rotate_secret: bool = False,
) -> tuple[str, str]:
    explicit = str(explicit_secret or "").strip()
    if explicit:
        return explicit, "--mail-ingest-secret"

    env_name = str(secret_env or "").strip()
    if env_name:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value, f"env:{env_name}"

    supplied_file = str(secret_file or "").strip()
    if supplied_file:
        value = Path(supplied_file).read_text(encoding="utf-8").strip()
        if not value:
            raise WorkerPlanError(f"Secret file is empty: {supplied_file}")
        return value, f"file:{supplied_file}"

    existing = out_dir / "secrets" / "mail_ingest_secret"
    if existing.exists() and not rotate_secret:
        value = existing.read_text(encoding="utf-8").strip()
        if value:
            return value, f"existing:{existing.as_posix()}"

    return generate_mail_ingest_secret(), "generated"


def contract_dict(plan: CloudflareMailWorkerPlan, routing: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "main-computer.cloudflare-mail-worker-contract.v1",
        "domain": plan.domain,
        "worker_name": plan.worker_name,
        "ingest_host": plan.ingest_host,
        "ingest_path": plan.ingest_path,
        "ingest_url": plan.ingest_url,
        "secret_binding": plan.secret_binding,
        "secret_header": plan.secret_header,
        "secret_file": "secrets/mail_ingest_secret",
        "user_registry_file": USER_REGISTRY_FILE,
        "coolify": {
            "service_name": plan.service_name,
            "compose_project": plan.compose_project,
            "maildir_domain": plan.maildir_domain,
            "mailbox_root": plan.mailbox_root,
            "listen_port": plan.listen_port,
            "coolify_url": plan.coolify_url,
        },
        "worker": {
            "name": plan.worker_name,
            "script": "worker/src/index.ts",
            "wrangler_toml": "worker/wrangler.toml",
            "env": {
                "MAIL_INGEST_URL": plan.ingest_url,
                "MAX_MESSAGE_BYTES": str(plan.max_message_bytes),
                "INGEST_FAILURE_POLICY": plan.failure_policy,
            },
            "secret_bindings": [plan.secret_binding],
        },
        "routing": routing,
        "artifacts": {
            "coolify_compose": "coolify/compose.yaml",
            "coolify_ingest_app": "coolify/mail_ingest.py",
            "manual_routing_plan": "cloudflare/manual-routing-plan.md",
            "worker_dashboard_paste": "cloudflare/worker-dashboard-paste.md",
            "wrangler_commands": "cloudflare/wrangler-commands.md",
        },
    }


def render_manual_routing_plan(plan: CloudflareMailWorkerPlan, routing: dict[str, Any]) -> str:
    forward_lines = []
    for local, destination in routing["forwards"].items():
        forward_lines.append(f"- `{local}@{plan.domain}` -> Send to an email -> `{destination}`")
    if not forward_lines:
        forward_lines.append("- No explicit email forwards were configured.")

    drop_lines = [f"- `{local}@{plan.domain}` -> Drop" for local in routing["drops"]]
    if not drop_lines:
        drop_lines.append("- No explicit drops were configured.")

    worker_lines = []
    for local in routing["worker_local_parts"]:
        worker_lines.append(f"- `{local}@{plan.domain}` -> Send to a Worker -> `{plan.worker_name}`")
    if routing["catch_all_to_worker"]:
        worker_lines.append(f"- Catch-all address for `*@{plan.domain}` -> Send to a Worker -> `{plan.worker_name}`")
    if not worker_lines:
        worker_lines.append("- No Worker routes were configured.")

    return textwrap.dedent(
        f"""\
        # Cloudflare manual routing plan for {plan.domain}

        This file is generated by `tools/cloudflare_mail_worker.py prepare`.
        It is the Cloudflare-side half of the stage-one contract. Coolify uses
        `mail-worker-contract.json` and the shared secret in `secrets/mail_ingest_secret`.

        ## Worker

        - Worker name: `{plan.worker_name}`
        - Worker source: `worker/src/index.ts`
        - Secret binding: `{plan.secret_binding}`
        - Secret value file: `secrets/mail_ingest_secret`
        - Variable `MAIL_INGEST_URL`: `{plan.ingest_url}`
        - Variable `MAX_MESSAGE_BYTES`: `{plan.max_message_bytes}`
        - Variable `INGEST_FAILURE_POLICY`: `{plan.failure_policy}`

        ## Routing rules

        Create these specific rules first:

        {chr(10).join(forward_lines)}

        {chr(10).join(drop_lines)}

        Then configure Worker routes:

        {chr(10).join(worker_lines)}

        ## Safe ordering

        1. Deploy/update the Coolify ingest service from this contract.
        2. Create/update the Cloudflare Worker using `worker/src/index.ts`.
        3. Add the Worker secret from `secrets/mail_ingest_secret`.
        4. Add explicit forward/drop rules.
        5. Enable the catch-all Worker route last.

        Cloudflare destination emails such as Gmail must be verified before routes
        that forward to them are active.
        """
    )


def render_worker_dashboard_paste(plan: CloudflareMailWorkerPlan) -> str:
    return textwrap.dedent(
        f"""\
        # Dashboard Worker creation values

        Use this when creating the Email Worker manually in Cloudflare.

        ## Worker name

        ```text
        {plan.worker_name}
        ```

        ## Starter

        Choose **Create my own**.

        ## Worker code

        Paste the contents of this JavaScript-compatible file:

        ```text
        worker/src/index.ts
        ```

        ## Variables and secrets

        Add these Worker variables:

        ```text
        MAIL_INGEST_URL={plan.ingest_url}
        MAX_MESSAGE_BYTES={plan.max_message_bytes}
        INGEST_FAILURE_POLICY={plan.failure_policy}
        ```

        Add this Worker secret:

        ```text
        {plan.secret_binding}=<contents of secrets/mail_ingest_secret>
        ```

        Keep the secret value out of tickets, screenshots, and git.
        """
    )


def render_wrangler_commands(plan: CloudflareMailWorkerPlan) -> str:
    return textwrap.dedent(
        f"""\
        # Optional Wrangler commands

        These commands are generated for repeatability. They are Cloudflare-side
        only; the Coolify ingest service should be deployed by the Python deployer
        from `mail-worker-contract.json`.

        ```bash
        cd worker
        npm install
        cat ../secrets/mail_ingest_secret | npx wrangler secret put {plan.secret_binding}
        npx wrangler deploy
        ```
        """
    )


def write_prepare_outputs(
    plan: CloudflareMailWorkerPlan,
    out_dir: Path,
    *,
    routing: dict[str, Any],
    secret: str,
) -> dict[str, Any]:
    write_outputs(plan, out_dir)

    secret_path = out_dir / "secrets" / "mail_ingest_secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(secret.strip() + "\n", encoding="utf-8")

    cloudflare_dir = out_dir / "cloudflare"
    cloudflare_dir.mkdir(parents=True, exist_ok=True)
    (cloudflare_dir / "manual-routing-plan.md").write_text(render_manual_routing_plan(plan, routing), encoding="utf-8")
    (cloudflare_dir / "worker-dashboard-paste.md").write_text(render_worker_dashboard_paste(plan), encoding="utf-8")
    (cloudflare_dir / "wrangler-commands.md").write_text(render_wrangler_commands(plan), encoding="utf-8")

    contract = contract_dict(plan, routing)
    contract_path = out_dir / "mail-worker-contract.json"
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    user_registry_path = ensure_mail_user_registry_file(contract, out_dir)

    return {
        "ok": True,
        "out": str(out_dir),
        "contract": str(contract_path),
        "secret_file": str(secret_path),
        "user_registry_file": str(user_registry_path),
        "domain": plan.domain,
        "worker_name": plan.worker_name,
        "ingest_url": plan.ingest_url,
        "routing": routing,
    }



def load_mail_worker_contract(contract_path: str | Path) -> tuple[dict[str, Any], Path]:
    path = Path(contract_path)
    if not str(path).strip():
        raise WorkerPlanError("Missing --contract.")
    if not path.exists():
        raise WorkerPlanError(f"Contract file does not exist: {path}")
    if not path.is_file():
        raise WorkerPlanError(f"Contract path is not a file: {path}")
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkerPlanError(f"Contract is not valid JSON: {path}") from exc
    if not isinstance(contract, dict):
        raise WorkerPlanError("Contract JSON must be an object.")
    schema = str(contract.get("schema") or "")
    if schema != "main-computer.cloudflare-mail-worker-contract.v1":
        raise WorkerPlanError(f"Unsupported contract schema: {schema!r}")
    return contract, path.parent


def _contract_int(value: Any, default: int, *, field: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WorkerPlanError(f"Contract field {field} must be an integer.") from exc


def _contract_str(mapping: dict[str, Any], key: str, default: str = "") -> str:
    value = mapping.get(key, default)
    return str(value if value is not None else default)


def plan_from_contract(contract: dict[str, Any], args: argparse.Namespace | None = None) -> CloudflareMailWorkerPlan:
    coolify = contract.get("coolify") if isinstance(contract.get("coolify"), dict) else {}
    worker = contract.get("worker") if isinstance(contract.get("worker"), dict) else {}
    worker_env = worker.get("env") if isinstance(worker.get("env"), dict) else {}
    routing = contract.get("routing") if isinstance(contract.get("routing"), dict) else {}

    coolify_url = _contract_str(coolify, "coolify_url")
    service_name = _contract_str(coolify, "service_name")
    compose_project = _contract_str(coolify, "compose_project")
    if args is not None:
        coolify_url = str(getattr(args, "coolify_url", "") or coolify_url)
        service_name = str(getattr(args, "coolify_service_name", "") or service_name)
        compose_project = str(getattr(args, "compose_project", "") or compose_project)

    return build_plan(
        domain=_contract_str(contract, "domain"),
        ingest_host=_contract_str(contract, "ingest_host"),
        ingest_path=_contract_str(contract, "ingest_path", DEFAULT_INGEST_PATH),
        worker_name=_contract_str(contract, "worker_name") or _contract_str(worker, "name"),
        secret_binding=_contract_str(contract, "secret_binding", DEFAULT_SECRET_NAME),
        secret_header=_contract_str(contract, "secret_header", "X-Main-Computer-Mail-Secret"),
        service_name=service_name,
        compose_project=compose_project,
        listen_port=_contract_int(coolify.get("listen_port"), DEFAULT_LISTEN_PORT, field="coolify.listen_port"),
        mailbox_root=_contract_str(coolify, "mailbox_root", "/var/mail"),
        maildir_domain=_contract_str(coolify, "maildir_domain") or _contract_str(contract, "domain"),
        max_message_bytes=_contract_int(worker_env.get("MAX_MESSAGE_BYTES"), DEFAULT_MAX_MESSAGE_BYTES, field="worker.env.MAX_MESSAGE_BYTES"),
        coolify_url=coolify_url,
        catch_all=bool(routing.get("catch_all_to_worker", True)),
        local_parts=routing.get("worker_local_parts", ()),
        failure_policy=_contract_str(worker_env, "INGEST_FAILURE_POLICY", "throw"),
    )


def _safe_relative_contract_path(value: str) -> Path:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise WorkerPlanError("Contract is missing secret_file.")
    posix = Path(raw)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in raw.split("/")):
        raise WorkerPlanError(f"Unsafe contract-relative path: {value!r}")
    return posix


def resolve_contract_secret(
    contract: dict[str, Any],
    contract_dir: Path,
    args: argparse.Namespace | None = None,
) -> tuple[str, str, Path | None]:
    if args is not None:
        explicit = str(getattr(args, "mail_ingest_secret", "") or "").strip()
        if explicit:
            return explicit, "--mail-ingest-secret", None

        env_name = str(getattr(args, "mail_ingest_secret_env", "") or "").strip()
        if env_name:
            value = os.environ.get(env_name, "").strip()
            if value:
                return value, f"env:{env_name}", None

        supplied_file = str(getattr(args, "mail_ingest_secret_file", "") or "").strip()
        if supplied_file:
            path = Path(supplied_file)
            value = path.read_text(encoding="utf-8").strip()
            if not value:
                raise WorkerPlanError(f"Secret file is empty: {path}")
            return value, f"file:{path}", path

    relative = _safe_relative_contract_path(str(contract.get("secret_file") or "secrets/mail_ingest_secret"))
    path = contract_dir / relative
    if not path.exists():
        raise WorkerPlanError(f"Contract secret file does not exist: {path}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise WorkerPlanError(f"Contract secret file is empty: {path}")
    return value, f"contract:{relative.as_posix()}", path



def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_mail_local(value: str, *, field: str = "local part", allow_reserved: bool = False) -> str:
    local = str(value or "").strip().lower()
    if not local:
        raise WorkerPlanError(f"Missing {field}.")
    if "@" in local:
        raise WorkerPlanError(f"{field} must be a local part, not a full email address.")
    if not re.match(r"^[a-z0-9][a-z0-9._-]{0,62}$", local):
        raise WorkerPlanError(
            f"Invalid {field}: {value!r}. Use 1-63 lowercase letters, numbers, dots, underscores, or hyphens."
        )
    if not allow_reserved and local in RESERVED_MAIL_LOCALS:
        reserved = ", ".join(RESERVED_MAIL_LOCALS)
        raise WorkerPlanError(f"Reserved {field}: {local!r}. Reserved names: {reserved}.")
    return local


def registry_relative_path(contract: dict[str, Any]) -> Path:
    return _safe_relative_contract_path(str(contract.get("user_registry_file") or USER_REGISTRY_FILE))


def user_registry_path_from_contract(contract: dict[str, Any], contract_dir: Path) -> Path:
    return contract_dir / registry_relative_path(contract)


def empty_mail_user_registry(domain: str) -> dict[str, Any]:
    clean_domain = normalize_domain(domain)
    return {
        "schema": USER_REGISTRY_SCHEMA,
        "domain": clean_domain,
        "users": {},
        "aliases": {},
        "reserved_locals": list(RESERVED_MAIL_LOCALS),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def normalize_mail_user_registry(registry: dict[str, Any], *, domain: str) -> dict[str, Any]:
    if not isinstance(registry, dict):
        raise WorkerPlanError("Mail user registry JSON must be an object.")
    schema = str(registry.get("schema") or "")
    if schema != USER_REGISTRY_SCHEMA:
        raise WorkerPlanError(f"Unsupported mail user registry schema: {schema!r}")
    clean_domain = normalize_domain(domain)
    registry_domain = normalize_domain(str(registry.get("domain") or clean_domain), field="registry domain")
    if registry_domain != clean_domain:
        raise WorkerPlanError(f"Mail user registry domain {registry_domain!r} does not match contract domain {clean_domain!r}.")
    registry.setdefault("domain", clean_domain)
    users = registry.get("users")
    aliases = registry.get("aliases")
    if not isinstance(users, dict):
        raise WorkerPlanError("Mail user registry field users must be an object.")
    if not isinstance(aliases, dict):
        raise WorkerPlanError("Mail user registry field aliases must be an object.")
    registry.setdefault("reserved_locals", list(RESERVED_MAIL_LOCALS))
    registry.setdefault("created_at", utc_now())
    registry.setdefault("updated_at", utc_now())
    return registry


def load_mail_user_registry(contract: dict[str, Any], contract_dir: Path) -> tuple[dict[str, Any], Path, bool]:
    domain = _contract_str(contract, "domain")
    path = user_registry_path_from_contract(contract, contract_dir)
    if not path.exists():
        return empty_mail_user_registry(domain), path, False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkerPlanError(f"Mail user registry is not valid JSON: {path}") from exc
    return normalize_mail_user_registry(raw, domain=domain), path, True


def save_mail_user_registry(registry: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["updated_at"] = utc_now()
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def ensure_mail_user_registry_file(contract: dict[str, Any], contract_dir: Path) -> Path:
    registry, path, existed = load_mail_user_registry(contract, contract_dir)
    if not existed:
        save_mail_user_registry(registry, path)
    return path


def password_hash(password: str, *, iterations: int = PASSWORD_HASH_ITERATIONS) -> str:
    raw = str(password)
    if len(raw) < PASSWORD_MIN_LENGTH:
        raise WorkerPlanError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise WorkerPlanError("Password must not contain NUL or newline characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt, iterations)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{PASSWORD_HASH_ALGORITHM}${iterations}${salt_b64}${digest_b64}"


def read_password_for_cli(args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "password", "") or "")
    if explicit:
        return explicit

    env_name = str(getattr(args, "password_env", "") or "").strip()
    if env_name:
        value = os.environ.get(env_name, "")
        if value:
            return value
        raise WorkerPlanError(f"Environment variable {env_name!r} is empty or unset.")

    file_name = str(getattr(args, "password_file", "") or "").strip()
    if file_name:
        path = Path(file_name)
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
        raise WorkerPlanError(f"Password file is empty: {path}")

    first = getpass.getpass("New mail password: ")
    second = getpass.getpass("Confirm new mail password: ")
    if first != second:
        raise WorkerPlanError("Passwords did not match.")
    return first


def user_record_public(record: dict[str, Any]) -> dict[str, Any]:
    public = dict(record)
    password_hash_value = str(public.pop("password_hash", "") or "")
    public["password_hash_set"] = bool(password_hash_value)
    return public


def registry_users_list(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [user_record_public(registry["users"][local]) for local in sorted(registry["users"])]


def registry_aliases_list(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(registry["aliases"][local]) for local in sorted(registry["aliases"])]


def registry_result(registry: dict[str, Any], path: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "schema": registry.get("schema"),
        "domain": registry.get("domain"),
        "registry_file": str(path),
        "users": registry_users_list(registry),
        "aliases": registry_aliases_list(registry),
    }


def create_mail_user(args: argparse.Namespace) -> dict[str, Any]:
    contract, contract_dir = load_mail_worker_contract(args.contract)
    registry, path, _ = load_mail_user_registry(contract, contract_dir)
    domain = _contract_str(contract, "domain")
    local = normalize_mail_local(args.local, field="user local part")
    users = registry["users"]
    aliases = registry["aliases"]
    if local in users:
        raise WorkerPlanError(f"Mail user already exists: {local}")
    if local in aliases:
        raise WorkerPlanError(f"Cannot create user {local!r}; that local part is already an alias.")
    now = utc_now()
    display_name = str(getattr(args, "display_name", "") or "").strip() or local
    users[local] = {
        "local": local,
        "address": f"{local}@{domain}",
        "display_name": display_name,
        "enabled": True,
        "mail_enabled": True,
        "password_hash": "",
        "password_set_at": "",
        "aliases": [],
        "created_at": now,
        "updated_at": now,
    }
    save_mail_user_registry(registry, path)
    return {
        "ok": True,
        "action": "user-add",
        "domain": domain,
        "registry_file": str(path),
        "user": user_record_public(users[local]),
    }


def list_mail_users(args: argparse.Namespace) -> dict[str, Any]:
    contract, contract_dir = load_mail_worker_contract(args.contract)
    registry, path, _ = load_mail_user_registry(contract, contract_dir)
    return registry_result(registry, path)


def set_mail_user_password(args: argparse.Namespace) -> dict[str, Any]:
    contract, contract_dir = load_mail_worker_contract(args.contract)
    registry, path, _ = load_mail_user_registry(contract, contract_dir)
    local = normalize_mail_local(args.local, field="user local part")
    users = registry["users"]
    if local not in users:
        raise WorkerPlanError(f"Mail user does not exist: {local}")
    users[local]["password_hash"] = password_hash(read_password_for_cli(args))
    users[local]["password_set_at"] = utc_now()
    users[local]["updated_at"] = utc_now()
    save_mail_user_registry(registry, path)
    return {
        "ok": True,
        "action": "user-password-set",
        "domain": registry["domain"],
        "registry_file": str(path),
        "user": user_record_public(users[local]),
    }


def add_mail_alias(args: argparse.Namespace) -> dict[str, Any]:
    contract, contract_dir = load_mail_worker_contract(args.contract)
    registry, path, _ = load_mail_user_registry(contract, contract_dir)
    domain = _contract_str(contract, "domain")
    alias = normalize_mail_local(args.alias, field="alias local part")
    target = normalize_mail_local(args.target, field="target user local part", allow_reserved=True)
    users = registry["users"]
    aliases = registry["aliases"]
    if target not in users:
        raise WorkerPlanError(f"Target mail user does not exist: {target}")
    if alias in users:
        raise WorkerPlanError(f"Cannot create alias {alias!r}; that local part is already a user.")
    if alias in aliases:
        raise WorkerPlanError(f"Mail alias already exists: {alias}")
    if alias == target:
        raise WorkerPlanError("Alias and target user must be different local parts.")
    now = utc_now()
    aliases[alias] = {
        "alias": alias,
        "address": f"{alias}@{domain}",
        "target": target,
        "target_address": users[target]["address"],
        "enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    user_aliases = set(users[target].get("aliases") or [])
    user_aliases.add(alias)
    users[target]["aliases"] = sorted(user_aliases)
    users[target]["updated_at"] = now
    save_mail_user_registry(registry, path)
    return {
        "ok": True,
        "action": "alias-add",
        "domain": domain,
        "registry_file": str(path),
        "alias": dict(aliases[alias]),
    }


def list_mail_aliases(args: argparse.Namespace) -> dict[str, Any]:
    contract, contract_dir = load_mail_worker_contract(args.contract)
    registry, path, _ = load_mail_user_registry(contract, contract_dir)
    return {
        "ok": True,
        "domain": registry["domain"],
        "registry_file": str(path),
        "aliases": registry_aliases_list(registry),
    }


def mail_user_registry_action(args: argparse.Namespace) -> dict[str, Any]:
    if args.action == "user-add":
        return create_mail_user(args)
    if args.action == "user-list":
        return list_mail_users(args)
    if args.action == "user-password-set":
        return set_mail_user_password(args)
    if args.action == "alias-add":
        return add_mail_alias(args)
    if args.action == "alias-list":
        return list_mail_aliases(args)
    raise WorkerPlanError(f"Unsupported mail registry action: {args.action}")


def args_with_contract_defaults(args: argparse.Namespace, contract: dict[str, Any]) -> argparse.Namespace:
    clone = copy.copy(args)
    coolify = contract.get("coolify") if isinstance(contract.get("coolify"), dict) else {}
    if not str(getattr(clone, "coolify_url", "") or "").strip():
        clone.coolify_url = _contract_str(coolify, "coolify_url")
    if not str(getattr(clone, "coolify_service_name", "") or "").strip():
        clone.coolify_service_name = _contract_str(coolify, "service_name")
    clone.coolify_url = normalize_coolify_url(str(getattr(clone, "coolify_url", "") or ""))
    return clone


def redact_text_secret(value: str, secret: str) -> str:
    clean_secret = str(secret or "")
    if not clean_secret:
        return value
    return str(value).replace(clean_secret, f"<redacted:{DEFAULT_SECRET_NAME}>")


def redact_nested_secret(value: Any, secret: str) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in {"docker_compose_raw", "docker_compose"}:
                result[key] = "<redacted:compose>"
            else:
                result[key] = redact_nested_secret(item, secret)
        return result
    if isinstance(value, list):
        return [redact_nested_secret(item, secret) for item in value]
    if isinstance(value, str):
        return redact_text_secret(value, secret)
    return value


def _contract_relative_path(contract_dir: Path, relative: str) -> Path:
    clean = str(relative or "").replace("\\", "/").strip()
    if not clean:
        return contract_dir
    pure = Path(clean)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise WorkerPlanError(f"Unsafe contract-relative path: {relative!r}")
    return contract_dir / pure


def _markdown_bullet_lines(values: list[str], *, empty: str) -> str:
    if not values:
        return f"- {empty}"
    return "\n".join(f"- {value}" for value in values)


def render_cloudflare_guide(
    plan: CloudflareMailWorkerPlan,
    contract: dict[str, Any],
    *,
    contract_path: Path,
    contract_dir: Path,
    secret: str,
    secret_path: Path | None,
    show_secret: bool = False,
) -> str:
    routing = contract.get("routing") if isinstance(contract.get("routing"), dict) else {}
    worker = contract.get("worker") if isinstance(contract.get("worker"), dict) else {}
    artifacts = contract.get("artifacts") if isinstance(contract.get("artifacts"), dict) else {}

    worker_script_rel = str(worker.get("script") or artifacts.get("worker_script") or "worker/src/index.ts")
    worker_script_path = _contract_relative_path(contract_dir, worker_script_rel)

    worker_env = worker.get("env") if isinstance(worker.get("env"), dict) else {}
    mail_ingest_url = str(worker_env.get("MAIL_INGEST_URL") or plan.ingest_url)
    max_message_bytes = str(worker_env.get("MAX_MESSAGE_BYTES") or plan.max_message_bytes)
    failure_policy = str(worker_env.get("INGEST_FAILURE_POLICY") or plan.failure_policy)

    forwards_raw = routing.get("forwards") if isinstance(routing.get("forwards"), dict) else {}
    forward_lines = [
        f"{safe_id(local, kind='forward local part')}@{plan.domain} -> forward to {normalize_email_address(destination)}"
        for local, destination in sorted(forwards_raw.items())
    ]

    drops_raw = routing.get("drops") if isinstance(routing.get("drops"), list) else []
    drop_lines = [
        f"{safe_id(str(local), kind='drop local part')}@{plan.domain} -> drop"
        for local in sorted(str(item) for item in drops_raw)
    ]

    worker_locals_raw = routing.get("worker_local_parts") if isinstance(routing.get("worker_local_parts"), list) else []
    worker_route_lines = [
        f"{safe_id(str(local), kind='worker local part')}@{plan.domain} -> worker {plan.worker_name}"
        for local in sorted(str(item) for item in worker_locals_raw)
    ]
    if bool(routing.get("catch_all_to_worker")):
        worker_route_lines.append(f"*@{plan.domain} -> worker {plan.worker_name}")

    if not forward_lines:
        forward_lines = ["none"]
    if not drop_lines:
        drop_lines = ["none"]
    if not worker_route_lines:
        worker_route_lines = ["none"]

    secret_display = secret if show_secret else f"<redacted; rerun with --show-secret>"

    return textwrap.dedent(
        f"""\
        Cloudflare setup for {plan.domain}

        Worker
        - Name: {plan.worker_name}
        - Source: {worker_script_path}
        - Secret: {plan.secret_binding}={secret_display}
        - Vars:
          MAIL_INGEST_URL={mail_ingest_url}
          MAX_MESSAGE_BYTES={max_message_bytes}
          INGEST_FAILURE_POLICY={failure_policy}

        Email Routing
        - Exact forwards:
        {textwrap.indent(chr(10).join("- " + line for line in forward_lines), "  ")}
        - Exact drops:
        {textwrap.indent(chr(10).join("- " + line for line in drop_lines), "  ")}
        - Worker routes:
        {textwrap.indent(chr(10).join("- " + line for line in worker_route_lines), "  ")}

        Checks
        - Health: https://{plan.ingest_host}/healthz
        - Order: create Worker, add exact routes, enable catch-all last.
        - Do not create a custom address named "*".
        """
    ).strip() + "\n"


def cloudflare_guide_from_contract(args: argparse.Namespace) -> str:
    contract, contract_dir = load_mail_worker_contract(getattr(args, "contract", ""))
    worker_args = args_with_contract_defaults(args, contract)
    plan = plan_from_contract(contract, worker_args)
    validate_plan(plan)
    secret, _secret_source, secret_path = resolve_contract_secret(contract, contract_dir, worker_args)
    return render_cloudflare_guide(
        plan,
        contract,
        contract_path=Path(getattr(args, "contract", "")),
        contract_dir=contract_dir,
        secret=secret,
        secret_path=secret_path,
        show_secret=bool(getattr(args, "show_secret", False)),
    )



def build_plan(
    *,
    domain: str,
    ingest_host: str = "",
    worker_name: str = "",
    ingest_path: str = DEFAULT_INGEST_PATH,
    secret_binding: str = DEFAULT_SECRET_NAME,
    secret_header: str = "X-Main-Computer-Mail-Secret",
    service_name: str = "",
    compose_project: str = "",
    listen_port: int = DEFAULT_LISTEN_PORT,
    mailbox_root: str = "/var/mail",
    maildir_domain: str = "",
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    coolify_url: str = "",
    catch_all: bool = True,
    local_parts: list[str] | tuple[str, ...] | str = (),
    failure_policy: str = "throw",
) -> CloudflareMailWorkerPlan:
    clean_domain = normalize_domain(domain)
    clean_ingest_host = normalize_host(ingest_host or f"mail-ingest.{clean_domain}", field="ingest-host")
    clean_path = normalize_path(ingest_path)
    clean_worker_name = safe_id(worker_name or f"{clean_domain}-mail-worker", kind="worker-name")
    clean_service_name = safe_id(service_name or f"{clean_domain}-mail-ingest", kind="service-name")
    clean_project = safe_id(compose_project or clean_service_name, kind="compose-project")
    clean_binding = str(secret_binding or DEFAULT_SECRET_NAME).strip()
    if not re.match(r"^[A-Z][A-Z0-9_]{0,63}$", clean_binding):
        raise WorkerPlanError(f"Secret binding must look like an environment variable name, got {secret_binding!r}.")
    clean_secret_header = str(secret_header or "").strip()
    if not re.match(r"^X-[A-Za-z0-9-]{1,80}$", clean_secret_header):
        raise WorkerPlanError("Secret header must be an X-* HTTP header.")
    clean_listen_port = int(listen_port)
    if clean_listen_port < 1 or clean_listen_port > 65535:
        raise WorkerPlanError("listen port must be in 1..65535.")
    clean_mailbox_root = str(mailbox_root or "/var/mail").strip()
    if not clean_mailbox_root.startswith("/") or "\x00" in clean_mailbox_root:
        raise WorkerPlanError("mailbox root must be an absolute POSIX path.")
    clean_maildir_domain = normalize_domain(maildir_domain or clean_domain, field="maildir-domain")
    clean_max = int(max_message_bytes)
    if clean_max < 1024 or clean_max > 100 * 1024 * 1024:
        raise WorkerPlanError("max message bytes must be between 1 KiB and 100 MiB.")
    clean_failure = str(failure_policy or "throw").strip().lower()
    if clean_failure not in {"throw", "reject"}:
        raise WorkerPlanError("failure policy must be 'throw' or 'reject'.")
    parts = parse_local_parts(local_parts)
    warnings = [
        "Cloudflare Email Routing becomes the public inbound SMTP edge; this does not forward SMTP to your origin.",
        "The generated ingest service stores raw RFC822 messages in Maildir format, but a Dovecot/webmail reader is a separate deployment step.",
        "Configure the Cloudflare routing rule in the dashboard or API: Action = Send to a Worker.",
    ]
    if not catch_all and not parts:
        warnings.append("No catch-all and no local parts were specified; create at least one Cloudflare routing rule.")
    if not coolify_url:
        warnings.append("No Coolify URL was supplied; write mode still renders artifacts for manual deployment.")

    ingest_url = f"https://{clean_ingest_host}{clean_path}"
    return CloudflareMailWorkerPlan(
        domain=clean_domain,
        worker_name=clean_worker_name,
        ingest_host=clean_ingest_host,
        ingest_path=clean_path,
        ingest_url=ingest_url,
        secret_binding=clean_binding,
        secret_header=clean_secret_header,
        service_name=clean_service_name,
        compose_project=clean_project,
        listen_port=clean_listen_port,
        mailbox_root=clean_mailbox_root,
        maildir_domain=clean_maildir_domain,
        max_message_bytes=clean_max,
        coolify_url=normalize_coolify_url(coolify_url),
        catch_all=bool(catch_all),
        local_parts=parts,
        failure_policy=clean_failure,
        warnings=tuple(warnings),
    )


def js_string(value: str) -> str:
    return json.dumps(value)


def render_worker_source(plan: CloudflareMailWorkerPlan) -> str:
    return textwrap.dedent(
        f'''\
        // @ts-nocheck
        // JavaScript-compatible Worker source.
        //
        // This file intentionally avoids TypeScript-only syntax so the same
        // source can be pasted into the Cloudflare dashboard editor and also
        // deployed by Wrangler.

        const SECRET_HEADER = {js_string(plan.secret_header)};

        export default {{
          async email(message, env, ctx) {{
            const maxBytes = Number(env.MAX_MESSAGE_BYTES || "{plan.max_message_bytes}");
            if (!Number.isFinite(maxBytes) || maxBytes <= 0) {{
              message.setReject("mail worker is misconfigured");
              return;
            }}
            if (message.rawSize > maxBytes) {{
              message.setReject("message too large");
              return;
            }}

            const ingestUrl = env.MAIL_INGEST_URL || {js_string(plan.ingest_url)};
            const secret = env.{plan.secret_binding};
            if (!secret) {{
              message.setReject("mail ingest secret is not configured");
              return;
            }}

            const headers = new Headers();
            headers.set("Content-Type", "message/rfc822");
            headers.set(SECRET_HEADER, secret);
            headers.set("X-Envelope-From", message.from);
            headers.set("X-Envelope-To", message.to);
            headers.set("X-Raw-Size", String(message.rawSize));
            const subject = message.headers.get("subject");
            const messageId = message.headers.get("message-id");
            if (subject) headers.set("X-Original-Subject", subject.slice(0, 512));
            if (messageId) headers.set("X-Original-Message-ID", messageId.slice(0, 512));

            let response;
            try {{
              response = await fetch(ingestUrl, {{
                method: "POST",
                headers,
                body: message.raw,
              }});
            }} catch (error) {{
              console.error("mail ingest request failed", error);
              if ((env.INGEST_FAILURE_POLICY || "{plan.failure_policy}") === "reject") {{
                message.setReject("mail ingest unavailable");
                return;
              }}
              throw error;
            }}

            if (!response.ok) {{
              const detail = await response.text().catch(() => "");
              console.error("mail ingest rejected message", response.status, detail.slice(0, 1024));
              if (response.status >= 400 && response.status < 500) {{
                message.setReject("mail ingest rejected message");
                return;
              }}
              if ((env.INGEST_FAILURE_POLICY || "{plan.failure_policy}") === "reject") {{
                message.setReject("mail ingest failed");
                return;
              }}
              throw new Error(`mail ingest failed with HTTP ${{response.status}}`);
            }}
          }},
        }};
        '''
    )


def render_wrangler_toml(plan: CloudflareMailWorkerPlan) -> str:
    return textwrap.dedent(
        f'''\
        name = "{plan.worker_name}"
        main = "src/index.ts"
        compatibility_date = "2026-06-01"
        workers_dev = false

        [vars]
        MAIL_INGEST_URL = "{plan.ingest_url}"
        MAX_MESSAGE_BYTES = "{plan.max_message_bytes}"
        INGEST_FAILURE_POLICY = "{plan.failure_policy}"
        '''
    )


def render_package_json(plan: CloudflareMailWorkerPlan) -> str:
    return json.dumps(
        {
            "name": plan.worker_name,
            "private": True,
            "scripts": {
                "deploy": "wrangler deploy",
                "dev": "wrangler dev",
                "typecheck": "tsc --noEmit",
            },
            "devDependencies": {
                "@cloudflare/workers-types": "^4.20260601.0",
                "typescript": "^5.8.3",
                "wrangler": "^4.0.0",
            },
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_tsconfig() -> str:
    return json.dumps(
        {
            "compilerOptions": {
                "target": "ES2022",
                "module": "ESNext",
                "moduleResolution": "Bundler",
                "strict": True,
                "types": ["@cloudflare/workers-types"],
                "noEmit": True,
                "skipLibCheck": True,
            },
            "include": ["src/**/*.ts"],
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_ingest_app(plan: CloudflareMailWorkerPlan) -> str:
    return textwrap.dedent(
        f'''\
        #!/usr/bin/env python3
        from __future__ import annotations

        import email.utils
        import hashlib
        import json
        import os
        import re
        import socket
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from pathlib import Path
        from urllib.parse import urlsplit


        SECRET = os.environ.get("MAIL_INGEST_SECRET", "")
        SECRET_HEADER = os.environ.get("MAIL_INGEST_SECRET_HEADER", "{plan.secret_header}")
        MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "{plan.maildir_domain}").lower().strip(".")
        MAILBOX_ROOT = Path(os.environ.get("MAILBOX_ROOT", "{plan.mailbox_root}"))
        MAX_MESSAGE_BYTES = int(os.environ.get("MAX_MESSAGE_BYTES", "{plan.max_message_bytes}"))
        INGEST_PATH = os.environ.get("INGEST_PATH", "{plan.ingest_path}")
        LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
        LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "{plan.listen_port}"))


        def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
            body = (json.dumps(payload, sort_keys=True) + "\\n").encode("utf-8")
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)


        def safe_local_part(value: str) -> str:
            local = value.strip().lower()
            if "+" in local:
                local = local.split("+", 1)[0]
            if not re.match(r"^[a-z0-9][a-z0-9._-]{{0,63}}$", local):
                raise ValueError("unsafe local part")
            if local in {{"postmaster", "abuse"}}:
                return local
            return local


        def parse_recipient(value: str) -> tuple[str, str]:
            _, addr = email.utils.parseaddr(value or "")
            if "@" not in addr:
                raise ValueError("missing recipient address")
            local, domain = addr.rsplit("@", 1)
            domain = domain.lower().strip(".")
            if domain != MAIL_DOMAIN:
                raise ValueError(f"unexpected recipient domain: {{domain}}")
            return safe_local_part(local), domain


        def ensure_maildir(local: str) -> Path:
            base = MAILBOX_ROOT / MAIL_DOMAIN / local / "Maildir"
            for sub in ("tmp", "new", "cur"):
                (base / sub).mkdir(parents=True, exist_ok=True, mode=0o700)
            return base


        def maildir_name(raw: bytes) -> str:
            return f"{{int(time.time())}}.M{{time.monotonic_ns()}}.{{os.getpid()}}.{{socket.gethostname()}},S={{len(raw)}}:2,"


        def deliver(raw: bytes, envelope_to: str) -> dict:
            local, domain = parse_recipient(envelope_to)
            maildir = ensure_maildir(local)
            name = maildir_name(raw)
            tmp_path = maildir / "tmp" / name
            new_path = maildir / "new" / name
            with tmp_path.open("xb") as handle:
                handle.write(raw)
            tmp_path.replace(new_path)
            return {{"ok": True, "domain": domain, "mailbox": local, "path": str(new_path), "bytes": len(raw)}}


        class Handler(BaseHTTPRequestHandler):
            server_version = "MainComputerMailIngest/1.0"

            def log_message(self, fmt: str, *args) -> None:
                print("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), fmt % args), flush=True)

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path == "/healthz":
                    json_response(self, 200, {{"ok": True, "service": "mail-ingest"}})
                    return
                json_response(self, 404, {{"ok": False, "error": "not_found"}})

            def do_POST(self) -> None:
                path = urlsplit(self.path).path
                if path != INGEST_PATH:
                    json_response(self, 404, {{"ok": False, "error": "not_found"}})
                    return
                if not SECRET:
                    json_response(self, 500, {{"ok": False, "error": "secret_not_configured"}})
                    return
                if self.headers.get(SECRET_HEADER, "") != SECRET:
                    json_response(self, 403, {{"ok": False, "error": "forbidden"}})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    json_response(self, 400, {{"ok": False, "error": "bad_content_length"}})
                    return
                if length <= 0:
                    json_response(self, 400, {{"ok": False, "error": "empty_message"}})
                    return
                if length > MAX_MESSAGE_BYTES:
                    json_response(self, 413, {{"ok": False, "error": "message_too_large"}})
                    return
                raw = self.rfile.read(length)
                envelope_to = self.headers.get("X-Envelope-To", "")
                try:
                    result = deliver(raw, envelope_to)
                except FileExistsError:
                    json_response(self, 409, {{"ok": False, "error": "duplicate_maildir_name"}})
                    return
                except Exception as exc:
                    json_response(self, 400, {{"ok": False, "error": "delivery_failed", "message": str(exc)}})
                    return
                json_response(self, 201, result)


        def main() -> None:
            if not MAIL_DOMAIN or "." not in MAIL_DOMAIN:
                raise SystemExit("MAIL_DOMAIN must be set to a valid domain")
            if not MAILBOX_ROOT.is_absolute():
                raise SystemExit("MAILBOX_ROOT must be absolute")
            httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
            print(f"mail ingest listening on {{LISTEN_HOST}}:{{LISTEN_PORT}} for {{MAIL_DOMAIN}}{{INGEST_PATH}}", flush=True)
            httpd.serve_forever()


        if __name__ == "__main__":
            main()
        '''
    )


def indent_block(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "".join(prefix + line if line.strip() else line for line in text.splitlines(True))


def yaml_quote(value: object) -> str:
    return json.dumps(str(value))


def traefik_label_id(value: str) -> str:
    clean = re.sub(r"[^a-z0-9-]+", "-", str(value or "").lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean or "mail-ingest"


def render_compose(plan: CloudflareMailWorkerPlan, *, mail_ingest_secret: str = "") -> str:
    app = render_ingest_app(plan).rstrip("\n")
    script_lines = "\n".join("        " + line for line in app.splitlines())
    secret_value = str(mail_ingest_secret or "").strip()
    if secret_value:
        secret_line = f"      MAIL_INGEST_SECRET: {yaml_quote(secret_value)}"
    else:
        secret_line = '      MAIL_INGEST_SECRET: "${MAIL_INGEST_SECRET:?set MAIL_INGEST_SECRET in Coolify secrets}"'
    router_id = traefik_label_id(plan.service_name)
    lines = [
        f"name: {plan.compose_project}",
        "services:",
        "  mail-ingest:",
        "    image: python:3.12-alpine",
        f"    container_name: {plan.service_name}",
        "    restart: unless-stopped",
        "    environment:",
        secret_line,
        f"      MAIL_INGEST_SECRET_HEADER: {yaml_quote(plan.secret_header)}",
        f"      MAIL_DOMAIN: {yaml_quote(plan.maildir_domain)}",
        f"      MAILBOX_ROOT: {yaml_quote(plan.mailbox_root)}",
        f"      MAX_MESSAGE_BYTES: {yaml_quote(plan.max_message_bytes)}",
        f"      INGEST_PATH: {yaml_quote(plan.ingest_path)}",
        '      LISTEN_HOST: "0.0.0.0"',
        f"      LISTEN_PORT: {yaml_quote(plan.listen_port)}",
        "    expose:",
        f'      - "{plan.listen_port}"',
        "    labels:",
        '      - "traefik.enable=true"',
        f'      - "traefik.http.routers.{router_id}.rule=Host(`{plan.ingest_host}`) && PathPrefix(`/`)"',
        f'      - "traefik.http.routers.{router_id}.entryPoints=https"',
        f'      - "traefik.http.routers.{router_id}.tls=true"',
        f'      - "traefik.http.routers.{router_id}.tls.certresolver=letsencrypt"',
        f'      - "traefik.http.services.{router_id}.loadbalancer.server.port={plan.listen_port}"',
        "    volumes:",
        f"      - mail-ingest-mailboxes:{plan.mailbox_root}",
        "    command:",
        "      - /bin/sh",
        "      - -ceu",
        "      - |",
        "        cat > /tmp/mail_ingest.py <<'PY'",
        script_lines,
        "        PY",
        "        exec python /tmp/mail_ingest.py",
        "    healthcheck:",
        f'      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:{plan.listen_port}/healthz\', timeout=3).read()"]',
        "      interval: 30s",
        "      timeout: 5s",
        "      retries: 3",
        "",
        "volumes:",
        "  mail-ingest-mailboxes:",
        "",
    ]
    return "\n".join(lines)

def render_routing_markdown(plan: CloudflareMailWorkerPlan) -> str:
    if plan.catch_all:
        routing = f"Create an active catch-all rule for `*@{plan.domain}` with Action `Send to a Worker` and Worker `{plan.worker_name}`."
    elif plan.local_parts:
        addrs = ", ".join(f"`{part}@{plan.domain}`" for part in plan.local_parts)
        routing = f"Create active routing rules for {addrs}; each rule should use Action `Send to a Worker` and Worker `{plan.worker_name}`."
    else:
        routing = "Create at least one routing rule with Action `Send to a Worker`."

    return textwrap.dedent(
        f'''\
        # Cloudflare Email Routing setup for {plan.domain}

        This mode hides the origin from inbound senders by making Cloudflare the public MX.
        It does not expose SMTP/25 on your Coolify host.

        ## Cloudflare dashboard

        1. Go to **Compute > Email Service > Email Routing**.
        2. Onboard `{plan.domain}`. Let Cloudflare add its MX, SPF, and DKIM records.
        3. Deploy the generated Worker from `worker/`.
        4. {routing}
        5. Point `https://{plan.ingest_host}` at the Coolify mail-ingest service through the Coolify HTTP proxy or through a Cloudflare Tunnel.
        6. Keep `{plan.ingest_host}` proxied in Cloudflare if it is a normal DNS hostname.

        ## Required Worker secret

        Use the same random value in both places:

        - Cloudflare Worker secret: `{plan.secret_binding}`
        - Coolify service secret: `MAIL_INGEST_SECRET`

        ## Ingest endpoint

        Worker POST target:

        ```text
        {plan.ingest_url}
        ```

        The Worker sends the raw RFC822 message as `Content-Type: message/rfc822`
        and includes envelope metadata in `X-Envelope-From` and `X-Envelope-To`.
        '''
    )


def sh(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def render_commands(plan: CloudflareMailWorkerPlan) -> str:
    coolify = plan.coolify_url or "http://144.126.212.9:8000"
    contract_path = f"runtime/cloudflare-mail-worker/{plan.domain}/mail-worker-contract.json"
    return textwrap.dedent(
        f"""\
        # Cloudflare mail worker operator commands

        # Stage one: prepare a reusable deployment contract.
        python tools/cloudflare_mail_worker.py prepare \\
          --domain {sh(plan.domain)} \\
          --ingest-host {sh(plan.ingest_host)} \\
          --worker-name {sh(plan.worker_name)} \\
          --coolify-url {sh(coolify)} \\
          --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN \\
          --coolify-environment mail \\
          --coolify-service-name {sh(plan.service_name)} \\
          --catch-all-to-worker \\
          --out runtime/cloudflare-mail-worker/{sh(plan.domain)}

        # Stage two: scripted Coolify deploy from the contract.
        python tools/cloudflare_mail_worker.py coolify-apply \
          --contract {sh(contract_path)} \
          --coolify-url {sh(coolify)} \
          --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN \
          --coolify-environment mail \
          --coolify-service-name {sh(plan.service_name)} \
          --dry-run

        # Then remove --dry-run to create/update/deploy the Coolify service.

        # Cloudflare-side manual files generated by stage one:
        #   runtime/cloudflare-mail-worker/{plan.domain}/cloudflare/worker-dashboard-paste.md
        #   runtime/cloudflare-mail-worker/{plan.domain}/cloudflare/manual-routing-plan.md
        #   runtime/cloudflare-mail-worker/{plan.domain}/cloudflare/wrangler-commands.md

        # Coolify discovery remains scripted and repeatable.
        python tools/cloudflare_mail_worker.py coolify-discover \\
          --domain {sh(plan.domain)} \\
          --ingest-host {sh(plan.ingest_host)} \\
          --coolify-url {sh(coolify)} \\
          --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN

        """
    )

def render_readme(plan: CloudflareMailWorkerPlan) -> str:
    return textwrap.dedent(
        f"""\
        # Cloudflare hidden mail worker

        Generated for `{plan.domain}`.

        ## Architecture

        ```text
        remote sender -> Cloudflare Email Routing MX -> Email Worker -> HTTPS POST -> Coolify mail-ingest -> Maildir
        ```

        ## Files

        - `mail-worker-contract.json` — stage-one handoff for scripted Coolify deployment and Cloudflare setup.
        - `secrets/mail_ingest_secret` — shared Worker/Coolify ingest secret; do not commit or paste publicly.
        - `worker/src/index.ts` — Cloudflare Email Worker.
        - `worker/wrangler.toml` — Wrangler project config.
        - `worker/package.json` and `worker/tsconfig.json` — local deploy/typecheck helpers.
        - `coolify/compose.yaml` — self-contained Coolify raw Compose service.
        - `coolify/mail_ingest.py` — the same ingest app embedded in the Compose command.
        - `cloudflare/manual-routing-plan.md` — exact forwards, drops, and Worker catch-all setup.
        - `cloudflare/worker-dashboard-paste.md` — dashboard Worker creation values.
        - `cloudflare/wrangler-commands.md` — optional Cloudflare-side Wrangler commands.
        - `operator-commands.txt` — generated runbook.
        - `plan.json` — machine-readable render plan.

        ## Important limitation

        This is not SMTP forwarding to the origin. Cloudflare receives inbound
        SMTP and invokes a Worker. The Worker posts the raw message over HTTPS.
        POP/IMAP/webmail access requires a separate reader layer over the Maildir
        volume or a later Dovecot/webmail deployment.

        ## Ingest endpoint

        `{plan.ingest_url}`

        """
    )

def write_outputs(plan: CloudflareMailWorkerPlan, out_dir: Path) -> None:
    files = {
        "README.md": render_readme(plan),
        "plan.json": json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n",
        "operator-commands.txt": render_commands(plan),
        "cloudflare-routing.md": render_routing_markdown(plan),
        "worker/src/index.ts": render_worker_source(plan),
        "worker/wrangler.toml": render_wrangler_toml(plan),
        "worker/package.json": render_package_json(plan),
        "worker/tsconfig.json": render_tsconfig(),
        "coolify/compose.yaml": render_compose(plan),
        "coolify/mail_ingest.py": render_ingest_app(plan),
    }
    for relative, content in files.items():
        path = out_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def validate_plan(plan: CloudflareMailWorkerPlan) -> None:
    if plan.domain == "example.com" or plan.ingest_host.endswith(".example.com"):
        raise WorkerPlanError("Refusing placeholder example.com.")
    if not plan.ingest_url.startswith("https://"):
        raise WorkerPlanError("Ingest URL must be HTTPS.")
    if plan.catch_all is False and not plan.local_parts:
        raise WorkerPlanError("Either enable catch-all or pass at least one --local-part.")
    if plan.ingest_host == plan.domain:
        raise WorkerPlanError("Use a subdomain such as mail-ingest.<domain> for ingest.")


def _args_with_normalized_coolify_url(args: argparse.Namespace) -> argparse.Namespace:
    clone = copy.copy(args)
    clone.coolify_url = normalize_coolify_url(str(getattr(args, "coolify_url", "") or ""))
    return clone


def resolve_worker_coolify_token(args: argparse.Namespace) -> tuple[str, str]:
    explicit = str(getattr(args, "coolify_token", "") or "").strip()
    if explicit:
        return explicit, "--coolify-token"
    env_name = str(getattr(args, "coolify_token_env", "") or DEFAULT_COOLIFY_TOKEN_ENV).strip()
    if env_name:
        import os

        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip(), f"env:{env_name}"
    token_file = str(getattr(args, "coolify_token_file", "") or "").strip()
    if token_file:
        value = Path(token_file).read_text(encoding="utf-8").strip()
        if value:
            return value, f"file:{token_file}"
    return "", "missing"


def coolify_client_from_worker_args(args: argparse.Namespace) -> tuple[CoolifyClient, str, str]:
    worker_args = _args_with_normalized_coolify_url(args)
    token, token_source = resolve_worker_coolify_token(worker_args)
    if not token:
        raise CoolifyMailError(
            f"Missing Coolify token. Set {getattr(worker_args, 'coolify_token_env', DEFAULT_COOLIFY_TOKEN_ENV)} "
            "or pass --coolify-token-file."
        )
    base_url = str(getattr(worker_args, "coolify_url", "") or "").strip()
    if not base_url:
        raise CoolifyMailError("Missing --coolify-url.")
    return (
        CoolifyClient(
            base_url,
            token,
            timeout_s=float(getattr(worker_args, "coolify_timeout_s", DEFAULT_COOLIFY_API_TIMEOUT_S)),
            retries=int(getattr(worker_args, "coolify_retries", DEFAULT_COOLIFY_API_RETRIES)),
            retry_sleep_s=float(getattr(worker_args, "coolify_retry_sleep_s", DEFAULT_COOLIFY_API_RETRY_SLEEP_S)),
        ),
        token,
        token_source,
    )


def coolify_check(args: argparse.Namespace) -> dict[str, Any]:
    client, token, token_source = coolify_client_from_worker_args(args)
    response = client.request("GET", "/api/v1/version")
    return {
        "ok": response.ok,
        "url": client.base_url,
        "token": redact_secret(token),
        "token_source": token_source,
        "response": response_to_dict(response),
    }


def coolify_discover(args: argparse.Namespace) -> dict[str, Any]:
    worker_args = _args_with_normalized_coolify_url(args)
    client, token, token_source = coolify_client_from_worker_args(worker_args)
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
    projects_response, projects = coolify_list(client, worker_args, "/api/v1/projects", label="projects", preferred_keys=("projects",))
    servers_response, servers = coolify_list(client, worker_args, "/api/v1/servers", label="servers", preferred_keys=("servers",))
    services_response, services = coolify_list(client, worker_args, "/api/v1/services", label="services", preferred_keys=("services",))
    environments_response: CoolifyResponse | None = None
    environments: list[dict[str, Any]] = []
    project_uuid, project_selection = ("", {"source": "api_error"})
    if projects_response.ok:
        project_uuid, project_selection = choose_coolify_uuid(
            explicit_uuid=str(getattr(worker_args, "coolify_project_uuid", "") or ""),
            explicit_name=str(getattr(worker_args, "coolify_project_name", "") or ""),
            items=projects,
            kind="project",
        )
    if project_uuid:
        environments_response, environments = coolify_list(
            client,
            worker_args,
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


def coolify_sync(
    plan: CloudflareMailWorkerPlan,
    args: argparse.Namespace,
    *,
    deploy: bool = False,
    mail_ingest_secret: str = "",
) -> dict[str, Any]:
    worker_args = _args_with_normalized_coolify_url(args)
    compose = render_compose(plan, mail_ingest_secret=mail_ingest_secret)
    compose_b64 = base64_compose(compose)
    service_name = str(getattr(worker_args, "coolify_service_name", "") or plan.service_name)
    service_uuid = str(getattr(worker_args, "coolify_service_uuid", "") or "").strip()

    if bool(getattr(worker_args, "dry_run", False)):
        return {
            "ok": True,
            "dry_run": True,
            "action": "coolify-sync",
            "service_name": service_name,
            "service_uuid": service_uuid,
            "coolify_url": str(getattr(worker_args, "coolify_url", "") or ""),
            "coolify_environment": str(getattr(worker_args, "coolify_environment", "") or "mail"),
            "compose": redact_text_secret(compose, mail_ingest_secret),
            "compose_base64_bytes": len(compose_b64),
            "secret_injected": bool(mail_ingest_secret),
            "secret": redact_secret(mail_ingest_secret) if mail_ingest_secret else "compose-placeholder",
            "next_steps": [
                f"Confirm https://{plan.ingest_host}/healthz through the Coolify HTTP proxy after deploy.",
                "Deploy the Cloudflare Worker with the matching secret from the contract.",
                "Configure Cloudflare Email Routing to send messages to the Worker.",
            ],
        }

    client, token, token_source = coolify_client_from_worker_args(worker_args)
    version = client.request("GET", "/api/v1/version")
    if not version.ok:
        return {"ok": False, "stage": "version", "response": response_to_dict(version, token=token, token_source=token_source)}

    tried: list[dict[str, Any]] = []
    create_context: dict[str, Any] = {}
    existing_service_context: dict[str, Any] = {}
    if not service_uuid:
        existing_service_uuid, existing_service_context = coolify_find_service_by_name(
            client=client,
            args=worker_args,
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
        create_refs, create_context = resolve_coolify_create_context(plan=plan, args=worker_args, client=client, tried=tried)
        create_context["existing_service"] = existing_service_context
        project_uuid = str(create_refs.get("project_uuid") or "").strip()
        server_uuid = str(create_refs.get("server_uuid") or "").strip()
        environment_uuid = str(create_refs.get("environment_uuid") or "").strip()
        if not project_uuid or not server_uuid or not environment_uuid:
            return {
                "ok": False,
                "stage": "missing-create-context",
                "message": (
                    "Coolify service creation requires project_uuid, server_uuid, and an environment. "
                    "If discovery returned multiple choices, rerun with --coolify-project-uuid/"
                    "--coolify-server-uuid/--coolify-environment-uuid or corresponding --*-name flags."
                ),
                "context": create_context,
                "tried": tried,
            }

        create_payload = {
            "server_uuid": server_uuid,
            "project_uuid": project_uuid,
            "environment_name": str(create_refs.get("environment_name") or "mail"),
            "environment_uuid": environment_uuid,
            "name": service_name,
            "description": f"Main Computer Cloudflare mail ingest for {plan.domain} generated by tools/cloudflare_mail_worker.py",
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
        "coolify_url": str(getattr(worker_args, "coolify_url", "") or ""),
        "environment": str(getattr(worker_args, "coolify_environment", "") or "mail"),
        "deployed": bool(deploy_result),
        "deploy_requested": bool(deploy_result),
        "deploy_result": deploy_result,
        "secret_injected": bool(mail_ingest_secret),
        "secret": redact_secret(mail_ingest_secret) if mail_ingest_secret else "compose-placeholder",
        "context": result_context,
        "tried": tried,
        "next_steps": [
            f"Confirm https://{plan.ingest_host}/healthz through the Coolify HTTP proxy after deploy.",
            "Deploy the Cloudflare Worker with the matching secret from the contract.",
            "Configure Cloudflare Email Routing to send mail to the Worker.",
        ],
    }


def coolify_apply_from_contract(args: argparse.Namespace) -> dict[str, Any]:
    contract, contract_dir = load_mail_worker_contract(getattr(args, "contract", ""))
    worker_args = args_with_contract_defaults(args, contract)
    plan = plan_from_contract(contract, worker_args)
    validate_plan(plan)
    secret, secret_source, secret_path = resolve_contract_secret(contract, contract_dir, worker_args)

    phases: list[dict[str, Any]] = []
    if bool(getattr(worker_args, "dry_run", False)):
        phases.append(
            {
                "phase": "coolify-check",
                "result": {
                    "ok": True,
                    "dry_run": True,
                    "url": normalize_coolify_url(str(getattr(worker_args, "coolify_url", "") or "")),
                },
            }
        )
    else:
        check_result = coolify_check(worker_args)
        phases.append({"phase": "coolify-check", "result": check_result})
        if not check_result.get("ok"):
            return {
                "ok": False,
                "domain": plan.domain,
                "contract": str(getattr(args, "contract", "")),
                "secret_source": secret_source,
                "phases": phases,
            }

    sync_result = coolify_sync(
        plan,
        worker_args,
        deploy=not bool(getattr(worker_args, "no_deploy", False)),
        mail_ingest_secret=secret,
    )
    sync_result = redact_nested_secret(sync_result, secret)
    phases.append({"phase": "coolify-sync", "result": sync_result})
    if not sync_result.get("ok"):
        return {
            "ok": False,
            "domain": plan.domain,
            "contract": str(getattr(args, "contract", "")),
            "secret_source": secret_source,
            "phases": phases,
        }

    return {
        "ok": True,
        "domain": plan.domain,
        "contract": str(getattr(args, "contract", "")),
        "contract_dir": str(contract_dir),
        "secret_source": secret_source,
        "secret_file": str(secret_path) if secret_path else "",
        "secret": redact_secret(secret),
        "ingest_url": plan.ingest_url,
        "worker_name": plan.worker_name,
        "coolify_service_name": str(getattr(worker_args, "coolify_service_name", "") or plan.service_name),
        "coolify_environment": str(getattr(worker_args, "coolify_environment", "") or "mail"),
        "phases": phases,
        "next_steps": [
            "Use cloudflare/worker-dashboard-paste.md or cloudflare/wrangler-commands.md for the Cloudflare Worker side.",
            "Use cloudflare/manual-routing-plan.md for exact forward/drop/catch-all routing.",
            f"After Cloudflare is configured, send a test message to a Worker-routed address at {plan.domain}.",
        ],
    }



def apply_mail_worker(plan: CloudflareMailWorkerPlan, args: argparse.Namespace) -> dict[str, Any]:
    phases: list[dict[str, Any]] = []
    if bool(getattr(args, "dry_run", False)):
        phases.append({"phase": "coolify-check", "result": {"ok": True, "dry_run": True, "url": normalize_coolify_url(str(getattr(args, "coolify_url", "") or ""))}})
    else:
        check_result = coolify_check(args)
        phases.append({"phase": "coolify-check", "result": check_result})
        if not check_result.get("ok"):
            return {"ok": False, "domain": plan.domain, "phases": phases}
    sync_result = coolify_sync(plan, args, deploy=not bool(getattr(args, "no_deploy", False)))
    phases.append({"phase": "coolify-sync", "result": sync_result})
    if not sync_result.get("ok"):
        return {"ok": False, "domain": plan.domain, "phases": phases}
    return {
        "ok": True,
        "domain": plan.domain,
        "ingest_url": plan.ingest_url,
        "phases": phases,
        "next_steps": [
            "Set the Coolify service domain and MAIL_INGEST_SECRET.",
            "Deploy the generated Cloudflare Worker with the same secret.",
            "Enable Cloudflare Email Routing and route addresses to the Worker.",
        ],
    }



def render_docs() -> str:
    return textwrap.dedent(
        """\
        Main Computer Cloudflare mail worker runbook
        ===========================================

        This generator creates the hidden-origin inbound mail path:

            sender -> Cloudflare Email Routing -> Email Worker -> HTTPS ingest -> Maildir

        Stage-one contract for greatlibrary.io:

            python tools/cloudflare_mail_worker.py prepare \\
              --domain greatlibrary.io \\
              --ingest-host mail-ingest.greatlibrary.io \\
              --worker-name greatlibrary-mail-ingest \\
              --coolify-url http://144.126.212.9:8000/projects \\
              --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN \\
              --coolify-environment mail \\
              --coolify-service-name greatlibrary-mail-ingest \\
              --forward-local johnrraymond \\
              --forward-to your-gmail-address@gmail.com \\
              --drop-local info \\
              --catch-all-to-worker \\
              --out runtime/cloudflare-mail-worker/greatlibrary.io

        The prepare action writes one reusable contract, one generated shared
        secret, Worker code/config, Coolify Compose, and Cloudflare manual
        routing instructions. It reuses an existing generated secret unless
        --rotate-secret is supplied.

        Discover Coolify context:

            python tools/cloudflare_mail_worker.py coolify-discover \\
              --domain greatlibrary.io \\
              --coolify-url http://144.126.212.9:8000/projects \\
              --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN

        Dry-run and apply the Coolify raw Compose service:

            python tools/cloudflare_mail_worker.py apply \\
              --domain greatlibrary.io \\
              --ingest-host mail-ingest.greatlibrary.io \\
              --coolify-url http://144.126.212.9:8000/projects \\
              --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN \\
              --coolify-project-name Default \\
              --coolify-server-name main-server \\
              --coolify-environment mail \\
              --coolify-service-name greatlibrary-mail-ingest \\
              --dry-run

        Then remove --dry-run to create/update the Coolify service.

        Coolify options mirror the other deployers:

          --coolify-token-env / --coolify-token-file / --coolify-token
          --coolify-project-uuid / --coolify-project-name
          --coolify-server-uuid / --coolify-server-name
          --coolify-destination-uuid
          --coolify-environment / --coolify-environment-uuid
          --coolify-service-uuid / --coolify-service-name

        The Coolify URL is normalized, so a UI URL ending in /projects is accepted.

        After stage one:

          1. Use mail-worker-contract.json as the handoff to the scripted Coolify stage.
          2. Use cloudflare-guide to print the exact dashboard values from the contract:

             python tools/cloudflare_mail_worker.py cloudflare-guide \
               --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json

             Add --show-secret only when you are ready to paste the Worker secret.
          3. Use cloudflare/manual-routing-plan.md for exact forwards, drops, and catch-all.
          4. Keep secrets/mail_ingest_secret out of screenshots, tickets, and git.

        This does not expose SMTP/25 on the origin and does not require the origin
        IP to appear in MX records.
        """
    )

def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a Cloudflare Email Worker and Coolify mail-ingest stack.")
    parser.add_argument(
        "action",
        nargs="?",
        default="docs",
        choices=["docs", "help", "plan", "worker", "wrangler", "compose", "ingest", "routing", "commands", "write", "prepare", "cloudflare-guide", "validate", "coolify-check", "coolify-discover", "coolify-sync", "coolify-apply", "apply", "user-add", "user-list", "user-password-set", "alias-add", "alias-list"],
    )
    parser.add_argument("--contract", default="", help="Stage-one mail-worker-contract.json for contract-based Coolify apply.")
    parser.add_argument("--domain", default="", help="Mail domain, e.g. greatlibrary.io.")
    parser.add_argument("--ingest-host", default="", help="HTTPS ingest hostname. Defaults to mail-ingest.<domain>.")
    parser.add_argument("--ingest-path", default=DEFAULT_INGEST_PATH)
    parser.add_argument("--worker-name", default="")
    parser.add_argument("--service-name", default="")
    parser.add_argument("--compose-project", default="")
    parser.add_argument("--secret-binding", default=DEFAULT_SECRET_NAME)
    parser.add_argument("--secret-header", default="X-Main-Computer-Mail-Secret")
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--mailbox-root", default="/var/mail")
    parser.add_argument("--maildir-domain", default="")
    parser.add_argument("--max-message-bytes", type=int, default=DEFAULT_MAX_MESSAGE_BYTES)
    parser.add_argument("--coolify-url", default="", help="Coolify base URL; UI paths like /projects are normalized to the API base.")
    parser.add_argument("--coolify-token", default="", help="Coolify bearer token. Prefer --coolify-token-env when possible.")
    parser.add_argument("--coolify-token-env", default=DEFAULT_COOLIFY_TOKEN_ENV, help="Environment variable containing the Coolify token.")
    parser.add_argument("--coolify-token-file", default="", help="File containing the Coolify token.")
    parser.add_argument("--coolify-timeout-s", type=float, default=DEFAULT_COOLIFY_API_TIMEOUT_S)
    parser.add_argument("--coolify-retries", type=int, default=DEFAULT_COOLIFY_API_RETRIES)
    parser.add_argument("--coolify-retry-sleep-s", type=float, default=DEFAULT_COOLIFY_API_RETRY_SLEEP_S)

    parser.add_argument("--coolify-service-uuid", default="", help="Existing Coolify service UUID to update/deploy.")
    parser.add_argument("--coolify-service-name", default="", help="Coolify service name to create/use. Also defaults the container/service name.")
    parser.add_argument("--coolify-project-uuid", default="", help="Project UUID for API service creation when no service UUID exists.")
    parser.add_argument("--coolify-project-name", default="", help="Project name to auto-select when discovering project UUID.")
    parser.add_argument("--coolify-server-uuid", default="", help="Server UUID for API service creation when no service UUID exists.")
    parser.add_argument("--coolify-server-name", default="", help="Server name to auto-select when discovering server UUID.")
    parser.add_argument("--coolify-destination-uuid", default="", help="Destination UUID for API service creation when no service UUID exists.")
    parser.add_argument("--coolify-environment", default="mail", help="Environment name for API service creation. Defaults to mail.")
    parser.add_argument("--coolify-environment-uuid", default="", help="Environment UUID for API service creation when the name is ambiguous.")
    parser.add_argument("--no-coolify-create-environment", action="store_true", help="Do not create the Coolify environment if it is missing.")

    parser.add_argument("--dry-run", action="store_true", help="Render/show intended Coolify changes without mutating Coolify.")
    parser.add_argument("--no-deploy", action="store_true", help="For coolify-sync/apply, update the service but do not trigger a deployment.")
    parser.add_argument("--quiet", action="store_true", help="Suppress operator progress logs; final JSON is still printed.")
    parser.add_argument("--show-secret", action="store_true", help="For cloudflare-guide, print the shared ingest secret for dashboard entry instead of redacting it.")

    parser.add_argument("--local", default="", help="Mail registry user local part for user-* actions.")
    parser.add_argument("--display-name", default="", help="Display name for user-add.")
    parser.add_argument("--alias", default="", help="Alias local part for alias-add.")
    parser.add_argument("--target", default="", help="Target user local part for alias-add.")
    parser.add_argument("--password", default="", help="Password for user-password-set. Prefer the interactive prompt, --password-env, or --password-file.")
    parser.add_argument("--password-env", default="", help="Environment variable containing the password for user-password-set.")
    parser.add_argument("--password-file", default="", help="File containing the password for user-password-set.")

    parser.add_argument("--no-catch-all", action="store_true")
    parser.add_argument("--catch-all-to-worker", action="store_true", help="Explicitly document catch-all Email Routing to the Worker. This is the default unless --no-catch-all is used.")
    parser.add_argument("--local-part", action="append", default=[], help="Mailbox local part to document for explicit Worker routing; repeat or comma-separate.")
    parser.add_argument("--forward", action="append", default=[], help="Stage-one routing shortcut: LOCAL=destination@example.com. Repeatable.")
    parser.add_argument("--forward-local", action="append", default=[], help="Local part to forward to --forward-to. Repeatable.")
    parser.add_argument("--forward-to", action="append", default=[], help="Verified destination email for the matching --forward-local. Repeatable.")
    parser.add_argument("--drop-local", action="append", default=[], help="Local part to explicitly drop before catch-all Worker routing; repeat or comma-separate.")
    parser.add_argument("--mail-ingest-secret", default="", help="Explicit shared ingest secret for prepare. Prefer generated, env, or file-backed secrets.")
    parser.add_argument("--mail-ingest-secret-env", default="", help="Environment variable containing the shared ingest secret for prepare.")
    parser.add_argument("--mail-ingest-secret-file", default="", help="File containing the shared ingest secret for prepare.")
    parser.add_argument("--rotate-secret", action="store_true", help="Generate a new secret instead of reusing an existing prepare output secret.")
    parser.add_argument("--failure-policy", choices=["throw", "reject"], default="throw")
    parser.add_argument("--out", default="", help="Output directory for write/prepare actions.")
    return parser.parse_args(argv)


def plan_from_args(args: argparse.Namespace) -> CloudflareMailWorkerPlan:
    return build_plan(
        domain=args.domain,
        ingest_host=args.ingest_host,
        ingest_path=args.ingest_path,
        worker_name=args.worker_name,
        secret_binding=args.secret_binding,
        secret_header=args.secret_header,
        service_name=args.service_name or args.coolify_service_name,
        compose_project=args.compose_project,
        listen_port=args.listen_port,
        mailbox_root=args.mailbox_root,
        maildir_domain=args.maildir_domain,
        max_message_bytes=args.max_message_bytes,
        coolify_url=args.coolify_url,
        catch_all=not bool(args.no_catch_all),
        local_parts=args.local_part,
        failure_policy=args.failure_policy,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.action in {"docs", "help"}:
        print(render_docs())
        return 0
    try:
        if args.action == "cloudflare-guide":
            print(cloudflare_guide_from_contract(args), end="")
            return 0
        if args.action == "coolify-apply":
            print_json(coolify_apply_from_contract(args))
            return 0
        if args.action in {"user-add", "user-list", "user-password-set", "alias-add", "alias-list"}:
            print_json(mail_user_registry_action(args))
            return 0
        plan = plan_from_args(args)
        if args.action == "validate":
            validate_plan(plan)
            print_json({"ok": True, "domain": plan.domain, "worker_name": plan.worker_name, "ingest_url": plan.ingest_url})
            return 0
        if args.action == "plan":
            print_json(plan.to_dict())
            return 0
        if args.action == "worker":
            print(render_worker_source(plan), end="")
            return 0
        if args.action == "wrangler":
            print(render_wrangler_toml(plan), end="")
            return 0
        if args.action == "compose":
            print(render_compose(plan), end="")
            return 0
        if args.action == "ingest":
            print(render_ingest_app(plan), end="")
            return 0
        if args.action == "routing":
            print(render_routing_markdown(plan), end="")
            return 0
        if args.action == "commands":
            print(render_commands(plan), end="")
            return 0
        if args.action == "write":
            out = Path(args.out or Path("runtime") / "cloudflare-mail-worker" / plan.domain)
            write_outputs(plan, out)
            print_json({"ok": True, "out": str(out), "domain": plan.domain, "worker_name": plan.worker_name, "ingest_url": plan.ingest_url})
            return 0
        if args.action == "prepare":
            out = Path(args.out or Path("runtime") / "cloudflare-mail-worker" / plan.domain)
            if args.no_catch_all and args.catch_all_to_worker:
                raise WorkerPlanError("--no-catch-all and --catch-all-to-worker conflict.")
            validate_plan(plan)
            forwards = parse_forward_routes(
                forward_pairs=args.forward,
                forward_locals=args.forward_local,
                forward_tos=args.forward_to,
            )
            drops = parse_local_parts(args.drop_local)
            routing = build_routing_contract(
                domain=plan.domain,
                worker_name=plan.worker_name,
                forwards=forwards,
                drops=drops,
                worker_local_parts=plan.local_parts,
                catch_all_to_worker=plan.catch_all,
            )
            secret, secret_source = resolve_prepare_secret(
                out_dir=out,
                explicit_secret=args.mail_ingest_secret,
                secret_env=args.mail_ingest_secret_env,
                secret_file=args.mail_ingest_secret_file,
                rotate_secret=bool(args.rotate_secret),
            )
            result = write_prepare_outputs(plan, out, routing=routing, secret=secret)
            result["secret_source"] = secret_source
            print_json(result)
            return 0
        if args.action == "coolify-check":
            print_json(coolify_check(args))
            return 0
        if args.action == "coolify-discover":
            print_json(coolify_discover(args))
            return 0
        if args.action == "coolify-sync":
            validate_plan(plan)
            print_json(coolify_sync(plan, args, deploy=not bool(args.no_deploy)))
            return 0
        if args.action == "apply":
            validate_plan(plan)
            print_json(apply_mail_worker(plan, args))
            return 0
    except (WorkerPlanError, CoolifyMailError) as exc:
        print(f"error: {exc}")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

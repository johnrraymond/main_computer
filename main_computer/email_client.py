from __future__ import annotations

import email
import imaplib
import poplib
import socket
import ssl
from email.header import decode_header, make_header
from typing import Any


_EMAIL_CHECK_TIMEOUT_SECONDS = 8
_EMAIL_CHECK_MAX_MESSAGES = 10
_EMAIL_ALLOWED_PROTOCOLS = {"imap", "pop3"}
_EMAIL_ALLOWED_SECURITY = {"ssl", "starttls", "none"}


class EmailClientConfigError(ValueError):
    """Raised when a live mail-check request is malformed or unsafe."""


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    if "\x00" in text:
        text = text.replace("\x00", "")
    return text[:limit]


def _clean_host(value: Any) -> str:
    host = _clean_text(value, limit=255).lower()
    if not host:
        raise EmailClientConfigError("incoming host is required.")
    if "://" in host or "/" in host or "\\" in host or any(char.isspace() for char in host):
        raise EmailClientConfigError("incoming host must be a host name or IP address, not a URL.")
    return host


def _clean_port(value: Any, *, field: str) -> int:
    try:
        port = int(str(value or "").strip())
    except (TypeError, ValueError):
        raise EmailClientConfigError(f"{field} port must be a number.") from None
    if port < 1 or port > 65535:
        raise EmailClientConfigError(f"{field} port must be between 1 and 65535.")
    return port


def _decoded_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def _message_from_headers(raw_headers: bytes, *, index: int, account_id: str, provider: str, username: str) -> dict[str, Any]:
    parsed = email.message_from_bytes(raw_headers)
    subject = _decoded_header(parsed.get("Subject")) or "(no subject)"
    sender = _decoded_header(parsed.get("From")) or "(unknown sender)"
    recipient = _decoded_header(parsed.get("To")) or username
    date = _decoded_header(parsed.get("Date")) or ""
    message_id = _decoded_header(parsed.get("Message-ID")) or f"{provider}-{index}"
    return {
        "id": f"live-{account_id}-{abs(hash((message_id, index))) % 10_000_000}",
        "accountId": account_id,
        "provider": provider,
        "folder": "inbox",
        "from": sender,
        "to": recipient,
        "subject": subject,
        "excerpt": "Fetched live message headers through the local backend mail bridge.",
        "body": "This preview intentionally contains headers only. Open the provider bridge implementation to add full body fetch and attachment handling.",
        "date": date,
        "labels": ["Live", provider.upper()],
        "priority": False,
        "unread": True,
    }


def normalize_email_check_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a one-time POP/IMAP check request.

    Passwords are accepted for the live check but are never included in the returned
    normalized summary.
    """

    if not isinstance(payload, dict):
        raise EmailClientConfigError("request payload must be an object.")

    protocol = _clean_text(payload.get("protocol") or "imap", limit=16).lower()
    if protocol not in _EMAIL_ALLOWED_PROTOCOLS:
        raise EmailClientConfigError("protocol must be imap or pop3.")

    security = _clean_text(payload.get("security") or "ssl", limit=16).lower()
    if security not in _EMAIL_ALLOWED_SECURITY:
        raise EmailClientConfigError("security must be ssl, starttls, or none.")

    host = _clean_host(payload.get("host"))
    default_port = 993 if protocol == "imap" and security == "ssl" else 995 if protocol == "pop3" and security == "ssl" else 143 if protocol == "imap" else 110
    port = _clean_port(payload.get("port") or default_port, field="incoming")

    username = _clean_text(payload.get("username") or payload.get("address"), limit=320)
    if not username:
        raise EmailClientConfigError("username is required.")

    password = str(payload.get("password") or "")
    if not password:
        raise EmailClientConfigError("password or app password is required for Check mail.")

    provider = _clean_text(payload.get("provider") or "custom", limit=64).lower() or "custom"
    account_id = _clean_text(payload.get("accountId") or payload.get("account_id") or provider, limit=128)

    return {
        "protocol": protocol,
        "security": security,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "provider": provider,
        "accountId": account_id,
    }


def public_email_check_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Return the safe, non-secret portion of a normalized mail check config."""

    return {
        "protocol": config["protocol"],
        "security": config["security"],
        "host": config["host"],
        "port": config["port"],
        "username": config["username"],
        "provider": config["provider"],
        "accountId": config["accountId"],
    }


def check_email_account(payload: dict[str, Any]) -> dict[str, Any]:
    """Check a POP3 or IMAP account and return a small header-only inbox preview.

    This is intentionally a one-shot bridge: the caller sends credentials for a
    check, the server uses them, and the response excludes secrets.
    """

    config = normalize_email_check_config(payload)
    try:
        if config["protocol"] == "imap":
            messages = _check_imap_headers(config)
        else:
            messages = _check_pop3_headers(config)
    except (imaplib.IMAP4.error, poplib.error_proto, OSError, socket.timeout, ssl.SSLError) as exc:
        raise EmailClientConfigError(f"mail check failed: {exc}") from exc

    return {
        "ok": True,
        "account": public_email_check_summary(config),
        "messages": messages,
        "count": len(messages),
    }


def _check_imap_headers(config: dict[str, Any]) -> list[dict[str, Any]]:
    if config["security"] == "ssl":
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(config["host"], config["port"], timeout=_EMAIL_CHECK_TIMEOUT_SECONDS)
    else:
        client = imaplib.IMAP4(config["host"], config["port"], timeout=_EMAIL_CHECK_TIMEOUT_SECONDS)
        if config["security"] == "starttls":
            client.starttls()

    try:
        client.login(config["username"], config["password"])
        client.select("INBOX", readonly=True)
        status, data = client.search(None, "ALL")
        if status != "OK" or not data:
            return []
        ids = data[0].split()[-_EMAIL_CHECK_MAX_MESSAGES:]
        messages: list[dict[str, Any]] = []
        for offset, message_id in enumerate(reversed(ids), start=1):
            fetch_status, parts = client.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
            if fetch_status != "OK" or not parts:
                continue
            raw = b""
            for part in parts:
                if isinstance(part, tuple) and isinstance(part[1], bytes):
                    raw += part[1]
            if raw:
                messages.append(_message_from_headers(raw, index=offset, account_id=config["accountId"], provider=config["provider"], username=config["username"]))
        return messages
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _check_pop3_headers(config: dict[str, Any]) -> list[dict[str, Any]]:
    if config["security"] == "ssl":
        client: poplib.POP3 = poplib.POP3_SSL(config["host"], config["port"], timeout=_EMAIL_CHECK_TIMEOUT_SECONDS)
    else:
        client = poplib.POP3(config["host"], config["port"], timeout=_EMAIL_CHECK_TIMEOUT_SECONDS)
        if config["security"] == "starttls":
            client.stls()

    try:
        client.user(config["username"])
        client.pass_(config["password"])
        count, _size = client.stat()
        start = max(1, count - _EMAIL_CHECK_MAX_MESSAGES + 1)
        messages: list[dict[str, Any]] = []
        for index, message_number in enumerate(range(count, start - 1, -1), start=1):
            _response, lines, _octets = client.top(message_number, 0)
            raw = b"\r\n".join(lines)
            if raw:
                messages.append(_message_from_headers(raw, index=index, account_id=config["accountId"], provider=config["provider"], username=config["username"]))
        return messages
    finally:
        try:
            client.quit()
        except Exception:
            pass

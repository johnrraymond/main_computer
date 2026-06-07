"""Public lexical seed data for Main Computer log redaction.

This committed file must never contain private usernames, handles, local paths,
hostnames, secrets, tokens, hashes of private strings, or machine-specific data.

Private/local redaction terms are loaded at runtime from either:

    MAIN_COMPUTER_LOG_PROTECTED_TERMS

or this ignored file:

    runtime/main_log_protected_terms.local.txt

The local file uses one term per line. Blank lines and lines beginning with "#"
are ignored.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


SEED_NAME = "main-log-public-seed-v1"

LEX_STRINGS: tuple[str, ...] = (
    "main-computer-service-supervisor",
    "main-computer",
    "main_computer",
    "service-supervisor",
    "service_supervisor",
    "runtime",
    "stdout",
    "stderr",
    "app.stdout.log",
    "app.stderr.log",
    "viewport",
    "onlyoffice",
    "docker",
    "wsl",
    "gitea",
    "directus",
    "coolify",
    "hub",
    "ledger",
    "credits",
    "xlag",
    "bridge",
    "wallet",
    "faucet",
    "rpc",
    "localhost",
    "127.0.0.1",
    "host.docker.internal",
)

# Public defaults only. Do not add private/local terms here.
_PUBLIC_PROTECTED_TERMS: tuple[str, ...] = ()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _split_terms(value: str) -> tuple[str, ...]:
    terms: list[str] = []

    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    for line in normalized.replace(";", "\n").replace(",", "\n").split("\n"):
        term = line.strip().strip('"').strip("'")
        if not term:
            continue
        if term.startswith("#"):
            continue
        # Avoid destructive over-redaction from accidental tiny fragments.
        if len(term) < 3:
            continue
        terms.append(term)

    return tuple(dict.fromkeys(terms))


def _load_env_terms() -> tuple[str, ...]:
    return _split_terms(os.environ.get("MAIN_COMPUTER_LOG_PROTECTED_TERMS", ""))


def _load_local_file_terms() -> tuple[str, ...]:
    path = _repo_root() / "runtime" / "main_log_protected_terms.local.txt"
    try:
        return _split_terms(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ()
    except OSError:
        return ()


def load_protected_terms() -> tuple[str, ...]:
    """Return public plus local/private protected terms without committing them."""

    return tuple(
        dict.fromkeys(
            (
                *_PUBLIC_PROTECTED_TERMS,
                *_load_env_terms(),
                *_load_local_file_terms(),
            )
        )
    )


PROTECTED_TERMS: tuple[str, ...] = load_protected_terms()

SEED_SOURCE_SHA256 = hashlib.sha256(
    SEED_NAME.encode("utf-8")
).hexdigest()

SEED_LEX_SHA256 = hashlib.sha256(
    "\n".join(LEX_STRINGS).encode("utf-8")
).hexdigest()
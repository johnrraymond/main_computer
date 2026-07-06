from __future__ import annotations

"""Live smoke test for the OpenClaw persistence surface Main Computer may use.

This intentionally tests OpenClaw before Main Computer grows an OpenClaw provider.
It proves whether OpenClaw can offer a persistence surface that is meaningfully
different from a plain Ollama-style model call.

There are two smoke modes:

* direct Markdown memory smoke (`--direct-memory`) writes a marker into the
  OpenClaw workspace memory files and scans those files. This is deterministic
  and is the default used by the Docker helper because it proves the persistence
  layer without depending on agent behavior.
* agent smoke calls `POST /v1/responses`, then checks session continuity and/or
  durable memory evidence. This is useful, but it is opt-in for Docker because
  local agent runs can be slow or can decide to use unrelated tools.

The durable file check is the strongest local signal because OpenClaw memory is
written to disk. Cross-session recall is useful, but it is still a model
behavior check and can fail if the agent does not search memory.
"""

import argparse
import datetime as _dt
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:18789"
DEFAULT_AGENT_ID = "main"
DEFAULT_TIMEOUT_S = 300.0
DEFAULT_POLL_S = 90.0
DEFAULT_MAX_OUTPUT_TOKENS = 128
USER_AGENT = "main-computer-openclaw-persistence-smoke/1"


class SmokeError(RuntimeError):
    """A smoke-test assertion or transport failure."""


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_memory_root() -> Path:
    return Path(os.environ.get("OPENCLAW_WORKSPACE", "~/.openclaw/workspace")).expanduser()


def clean_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SmokeError("--base-url must look like http://host:port or https://host")
    return base_url.rstrip("/")


def join_url(base_url: str, path: str) -> str:
    return clean_base_url(base_url) + "/" + path.lstrip("/")


def bearer_token_from_args(value: str | None) -> str | None:
    if value:
        return value
    for name in (
        "MAIN_COMPUTER_OPENCLAW_TOKEN",
        "OPENCLAW_GATEWAY_TOKEN",
        "OPENCLAW_API_KEY",
        "OPENCLAW_TOKEN",
    ):
        candidate = os.environ.get(name)
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def build_headers(
    *,
    token: str | None,
    agent_id: str,
    session_key: str,
    backend_model: str | None,
    message_channel: str | None,
) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "x-openclaw-agent-id": agent_id,
        "x-openclaw-session-key": session_key,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if backend_model:
        headers["x-openclaw-model"] = backend_model
    if message_channel:
        headers["x-openclaw-message-channel"] = message_channel
    return headers


def post_json(
    *,
    base_url: str,
    token: str | None,
    agent_id: str,
    session_key: str,
    backend_model: str | None,
    message_channel: str | None,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        join_url(base_url, "/v1/responses"),
        data=json.dumps(payload).encode("utf-8"),
        headers=build_headers(
            token=token,
            agent_id=agent_id,
            session_key=session_key,
            backend_model=backend_model,
            message_channel=message_channel,
        ),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise SmokeError(f"POST /v1/responses failed with HTTP {exc.code}: {body[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise SmokeError(f"POST /v1/responses failed: {exc}") from exc
    except TimeoutError as exc:
        raise SmokeError(f"POST /v1/responses timed out after {timeout} seconds") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SmokeError(f"POST /v1/responses did not return JSON: {raw[:500]!r}") from exc
    if not isinstance(data, dict):
        raise SmokeError("POST /v1/responses returned a non-object JSON payload")
    return data


def extract_text(value: Any) -> str:
    """Extract useful text from OpenResponses/OpenAI-compatible response shapes."""

    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""

    parts: list[str] = []
    output_text = value.get("output_text")
    if isinstance(output_text, str):
        parts.append(output_text)

    content = value.get("content")
    if isinstance(content, str):
        parts.append(content)

    output = value.get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_text = item.get("text")
            if isinstance(item_text, str):
                parts.append(item_text)
            item_content = item.get("content")
            if isinstance(item_content, str):
                parts.append(item_content)
            elif isinstance(item_content, list):
                for chunk in item_content:
                    if isinstance(chunk, str):
                        parts.append(chunk)
                    elif isinstance(chunk, dict):
                        chunk_text = chunk.get("text") or chunk.get("output_text")
                        if isinstance(chunk_text, str):
                            parts.append(chunk_text)

    choices = value.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                parts.append(message["content"])
            if isinstance(choice.get("text"), str):
                parts.append(choice["text"])

    if parts:
        return "\n".join(part.strip() for part in parts if part.strip())
    return json.dumps(value, sort_keys=True)[:4000]


def marker_in_text(marker: str, text: str) -> bool:
    return marker.lower() in text.lower()


def iter_memory_files(memory_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for relative in ("MEMORY.md", "DREAMS.md"):
        path = memory_root / relative
        if path.is_file():
            candidates.append(path)
    memory_dir = memory_root / "memory"
    if memory_dir.is_dir():
        candidates.extend(path for path in sorted(memory_dir.glob("*.md")) if path.is_file())
    return sorted(set(candidates))


def scan_memory_files(memory_root: Path, marker: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    files = iter_memory_files(memory_root)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            matches.append({"path": str(path), "read_error": str(exc)})
            continue
        lower = text.lower()
        marker_lower = marker.lower()
        if marker_lower not in lower:
            continue
        index = lower.find(marker_lower)
        snippet_start = max(0, index - 120)
        snippet_end = min(len(text), index + len(marker) + 120)
        matches.append(
            {
                "path": str(path),
                "relative_path": str(path.relative_to(memory_root)) if path.is_relative_to(memory_root) else str(path),
                "snippet": text[snippet_start:snippet_end].replace("\n", "\\n"),
            }
        )
    return {
        "checked": True,
        "memory_root": str(memory_root),
        "file_count": len(files),
        "matches": matches,
        "found": any("snippet" in match for match in matches),
    }


def poll_memory_files(memory_root: Path, marker: str, *, poll_s: float, interval_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, poll_s)
    last = scan_memory_files(memory_root, marker)
    while not last["found"] and time.monotonic() < deadline:
        time.sleep(max(0.5, interval_s))
        last = scan_memory_files(memory_root, marker)
    last["poll_s"] = poll_s
    return last


def run_openclaw_memory_search(
    *,
    marker: str,
    agent_id: str,
    timeout: float,
    command: str,
) -> dict[str, Any]:
    args = [command, "memory", "search", marker, "--agent", agent_id]
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"checked": True, "available": False, "error": f"{command!r} not found"}
    except subprocess.TimeoutExpired:
        return {"checked": True, "available": True, "error": f"timed out after {timeout} seconds"}

    text = (completed.stdout or "") + "\n" + (completed.stderr or "")
    return {
        "checked": True,
        "available": True,
        "returncode": completed.returncode,
        "found": marker_in_text(marker, text),
        "preview": text[:2000],
    }


def write_direct_memory_fact(memory_root: Path, *, marker: str, phrase: str) -> Path:
    """Append a smoke-test fact directly to the OpenClaw Markdown memory layer."""

    memory_dir = memory_root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    path = memory_dir / f"{today}-main-computer-smoke.md"
    timestamp = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    entry = (
        "\n"
        "## Main Computer persistence smoke\n\n"
        f"- Timestamp: {timestamp}\n"
        f"- Marker: {marker}\n"
        f"- Phrase: {phrase}\n"
        "- Purpose: validate that Main Computer can write and later read OpenClaw's durable Markdown memory surface.\n"
    )
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(entry)
    return path


def run_direct_memory_smoke(
    *,
    memory_root: Path,
    memory_poll_s: float,
    memory_poll_interval_s: float,
) -> dict[str, Any]:
    """Run a deterministic local persistence proof without contacting an LLM."""

    memory_root = memory_root.expanduser()
    marker = f"MC_OPENCLAW_DIRECT_MEMORY_SMOKE_{utc_stamp()}_{secrets.token_hex(5)}"
    phrase = f"main-computer-direct-memory-phrase-{secrets.token_hex(4)}"
    written_file = write_direct_memory_fact(memory_root, marker=marker, phrase=phrase)
    memory_files = poll_memory_files(
        memory_root,
        marker,
        poll_s=memory_poll_s,
        interval_s=memory_poll_interval_s,
    )
    found = bool(memory_files.get("found")) and any(
        marker_in_text(phrase, match.get("snippet", "")) for match in memory_files.get("matches", [])
    )
    result = {
        "ok": found,
        "smoke": "openclaw-persistence-direct-memory",
        "marker": marker,
        "phrase": phrase,
        "memory_root": str(memory_root),
        "written_file": str(written_file),
        "proved": [
            "marker was written to OpenClaw's Markdown memory workspace",
            "marker was read back from the same durable memory files",
        ]
        if found
        else [],
        "checks": {
            "memory_files": memory_files,
            "phrase_found_in_marker_snippet": found,
        },
    }
    if not found:
        result["failures"] = ["direct Markdown memory write could not be read back"]
    return result


def responses_payload(
    *,
    model: str,
    user: str,
    instructions: str,
    prompt: str,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "user": user,
        "instructions": instructions,
        "input": prompt,
    }
    if max_output_tokens and max_output_tokens > 0:
        payload["max_output_tokens"] = max_output_tokens
    return payload


def run_smoke(
    *,
    base_url: str,
    token: str | None,
    agent_id: str,
    backend_model: str | None,
    session_key: str,
    memory_root: Path,
    check_memory_files: bool,
    memory_poll_s: float,
    memory_poll_interval_s: float,
    run_memory_cli: bool,
    openclaw_command: str,
    timeout: float,
    require_cross_session_recall: bool,
    message_channel: str | None,
    max_output_tokens: int | None,
    skip_recall_turns: bool,
) -> dict[str, Any]:
    base_url = clean_base_url(base_url)
    marker = f"MC_OPENCLAW_PERSISTENCE_SMOKE_{utc_stamp()}_{secrets.token_hex(5)}"
    phrase = f"main-computer-persistence-phrase-{secrets.token_hex(4)}"
    model = f"openclaw/{agent_id}" if agent_id else "openclaw/default"
    fresh_session_key = f"{session_key}-fresh-{secrets.token_hex(4)}"
    instructions = (
        "Main Computer/OpenClaw persistence smoke. "
        "Save the requested marker to durable OpenClaw memory if a memory tool is available. "
        "Reply briefly with only the marker and phrase. Do not execute shell commands."
    )

    write_prompt = (
        "Save this exact smoke-test fact to durable OpenClaw memory. "
        f"Marker: {marker}. Phrase: {phrase}. "
        "Reply with only the marker and phrase."
    )
    write_response = post_json(
        base_url=base_url,
        token=token,
        agent_id=agent_id,
        session_key=session_key,
        backend_model=backend_model,
        message_channel=message_channel,
        payload=responses_payload(
            model=model,
            user=session_key,
            instructions=instructions,
            prompt=write_prompt,
            max_output_tokens=max_output_tokens,
        ),
        timeout=timeout,
    )
    write_text = extract_text(write_response)

    same_session_found = False
    same_session_text = ""
    if not skip_recall_turns:
        same_session_prompt = (
            "Using the current session context, repeat the exact Main Computer persistence smoke marker and phrase."
        )
        same_session_response = post_json(
            base_url=base_url,
            token=token,
            agent_id=agent_id,
            session_key=session_key,
            backend_model=backend_model,
            message_channel=message_channel,
            payload=responses_payload(
                model=model,
                user=session_key,
                instructions=instructions,
                prompt=same_session_prompt,
                max_output_tokens=max_output_tokens,
            ),
            timeout=timeout,
        )
        same_session_text = extract_text(same_session_response)
        same_session_found = marker_in_text(marker, same_session_text) and marker_in_text(phrase, same_session_text)

    memory_files: dict[str, Any] = {"checked": False, "found": False}
    if check_memory_files:
        memory_files = poll_memory_files(
            memory_root,
            marker,
            poll_s=memory_poll_s,
            interval_s=memory_poll_interval_s,
        )

    memory_cli: dict[str, Any] = {"checked": False, "found": False}
    if run_memory_cli:
        memory_cli = run_openclaw_memory_search(
            marker=marker,
            agent_id=agent_id,
            timeout=timeout,
            command=openclaw_command,
        )

    fresh_session_found = False
    fresh_text = ""
    if not skip_recall_turns:
        fresh_prompt = (
            "This is a fresh session. Use durable OpenClaw memory if needed. "
            "What exact Main Computer persistence smoke marker and phrase were saved earlier? "
            "Return only the marker and phrase if you can find them."
        )
        fresh_response = post_json(
            base_url=base_url,
            token=token,
            agent_id=agent_id,
            session_key=fresh_session_key,
            backend_model=backend_model,
            message_channel=message_channel,
            payload=responses_payload(
                model=model,
                user=fresh_session_key,
                instructions=instructions,
                prompt=fresh_prompt,
                max_output_tokens=max_output_tokens,
            ),
            timeout=timeout,
        )
        fresh_text = extract_text(fresh_response)
        fresh_session_found = marker_in_text(marker, fresh_text) and marker_in_text(phrase, fresh_text)

    durable_found = bool(memory_files.get("found")) or bool(memory_cli.get("found")) or fresh_session_found
    ok = bool(durable_found) if skip_recall_turns else bool(same_session_found and durable_found)
    if require_cross_session_recall:
        ok = bool(ok and fresh_session_found)

    proved: list[str] = []
    if same_session_found:
        proved.append("stable x-openclaw-session-key preserved current-session context")
    if memory_files.get("found"):
        proved.append("marker was written to local OpenClaw Markdown memory files")
    if memory_cli.get("found"):
        proved.append("openclaw memory search found the marker")
    if fresh_session_found:
        proved.append("a different session recalled the marker through persistence")

    result = {
        "ok": ok,
        "smoke": "openclaw-persistence-surface",
        "base_url": base_url,
        "agent_id": agent_id,
        "backend_model_override": backend_model,
        "session_key": session_key,
        "fresh_session_key": fresh_session_key,
        "marker": marker,
        "phrase": phrase,
        "proved": proved,
        "checks": {
            "write_response_had_marker": marker_in_text(marker, write_text),
            "same_session_checked": not skip_recall_turns,
            "same_session_found": same_session_found,
            "durable_found": durable_found,
            "fresh_session_checked": not skip_recall_turns,
            "fresh_session_found": fresh_session_found,
            "memory_files": memory_files,
            "memory_cli": memory_cli,
        },
        "previews": {
            "write": write_text[:1200],
            "same_session": same_session_text[:1200],
            "fresh_session": fresh_text[:1200],
        },
    }
    if not ok:
        failures: list[str] = []
        if (not skip_recall_turns) and (not same_session_found):
            failures.append("same-session recall did not contain the marker and phrase")
        if not durable_found:
            failures.append("no durable persistence evidence was found")
        if require_cross_session_recall and not fresh_session_found:
            failures.append("--require-cross-session-recall was set and fresh-session recall failed")
        result["failures"] = failures
    return result


def run_self_test() -> dict[str, Any]:
    sample = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "marker abc phrase def"},
                ],
            }
        ]
    }
    text = extract_text(sample)
    if "marker abc" not in text:
        raise SmokeError("self-test failed to extract OpenResponses text")

    root = Path(tempfile.gettempdir()) / f"openclaw-persistence-smoke-selftest-{secrets.token_hex(4)}"
    try:
        (root / "memory").mkdir(parents=True, exist_ok=True)
        (root / "memory" / "2099-01-01.md").write_text("hello MC_OPENCLAW_PERSISTENCE_SMOKE_SELFTEST\n", encoding="utf-8")
        scan = scan_memory_files(root, "MC_OPENCLAW_PERSISTENCE_SMOKE_SELFTEST")
        if not scan["found"]:
            raise SmokeError("self-test failed to find marker in memory file")
        direct = run_direct_memory_smoke(
            memory_root=root,
            memory_poll_s=0.0,
            memory_poll_interval_s=0.5,
        )
        if not direct["ok"]:
            raise SmokeError("self-test failed direct memory smoke")
    finally:
        try:
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            root.rmdir()
        except OSError:
            pass

    return {
        "ok": True,
        "smoke": "openclaw-persistence-surface-self-test",
        "proved": [
            "OpenResponses text extraction handles message/content arrays",
            "Markdown memory file scanning finds smoke markers",
            "direct Markdown memory smoke writes and reads a durable marker",
        ],
    }


def print_result(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    if result.get("ok"):
        print("OpenClaw persistence smoke passed.")
    else:
        print("OpenClaw persistence smoke failed.", file=sys.stderr)
    for item in result.get("proved", []):
        print(f"- {item}")
    for item in result.get("failures", []):
        print(f"- failure: {item}", file=sys.stderr)
    print(f"marker: {result.get('marker', '(none)')}")
    checks = result.get("checks")
    if isinstance(checks, dict):
        print("checks:")
        print(json.dumps(checks, indent=2, sort_keys=True))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test OpenClaw Gateway session and durable memory before adding a Main Computer provider.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MAIN_COMPUTER_OPENCLAW_BASE_URL")
        or os.environ.get("OPENCLAW_GATEWAY_URL")
        or DEFAULT_BASE_URL,
        help=f"OpenClaw Gateway base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Gateway bearer token. Defaults to MAIN_COMPUTER_OPENCLAW_TOKEN, OPENCLAW_GATEWAY_TOKEN, OPENCLAW_API_KEY, or OPENCLAW_TOKEN.",
    )
    parser.add_argument(
        "--agent-id",
        default=os.environ.get("MAIN_COMPUTER_OPENCLAW_AGENT_ID", DEFAULT_AGENT_ID),
        help=f"OpenClaw agent id. Default: {DEFAULT_AGENT_ID}",
    )
    parser.add_argument(
        "--backend-model",
        default=os.environ.get("MAIN_COMPUTER_OPENCLAW_BACKEND_MODEL"),
        help="Optional x-openclaw-model backend override, for example ollama/qwen3:32b.",
    )
    parser.add_argument(
        "--session-key",
        default=os.environ.get("MAIN_COMPUTER_OPENCLAW_SESSION_KEY", "main-computer-persistence-smoke"),
        help="Stable x-openclaw-session-key to exercise.",
    )
    parser.add_argument(
        "--message-channel",
        default=os.environ.get("MAIN_COMPUTER_OPENCLAW_MESSAGE_CHANNEL"),
        help="Optional x-openclaw-message-channel synthetic ingress channel.",
    )
    parser.add_argument(
        "--memory-root",
        type=Path,
        default=default_memory_root(),
        help="Local OpenClaw agent workspace to scan for MEMORY.md and memory/*.md. Default: ~/.openclaw/workspace or OPENCLAW_WORKSPACE.",
    )
    parser.add_argument(
        "--skip-memory-file-check",
        action="store_true",
        help="Do not scan local Markdown memory files. Useful when testing a remote Gateway.",
    )
    parser.add_argument(
        "--memory-poll-s",
        type=float,
        default=DEFAULT_POLL_S,
        help=f"Seconds to poll local memory files after the write turn. Default: {DEFAULT_POLL_S}.",
    )
    parser.add_argument(
        "--memory-poll-interval-s",
        type=float,
        default=3.0,
        help="Seconds between local memory-file scans.",
    )
    parser.add_argument(
        "--memory-cli",
        action="store_true",
        help="Also run `openclaw memory search <marker> --agent <agent-id>`.",
    )
    parser.add_argument(
        "--openclaw-command",
        default=os.environ.get("OPENCLAW_COMMAND", "openclaw"),
        help="OpenClaw CLI command for --memory-cli. Default: openclaw.",
    )
    parser.add_argument(
        "--require-cross-session-recall",
        action="store_true",
        help="Fail unless a different session recalls the marker and phrase.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"HTTP/CLI timeout seconds. Default: {DEFAULT_TIMEOUT_S}.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(os.environ.get("MAIN_COMPUTER_OPENCLAW_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)),
        help=f"Maximum output tokens requested from /v1/responses. Default: {DEFAULT_MAX_OUTPUT_TOKENS}.",
    )
    parser.add_argument(
        "--skip-recall-turns",
        action="store_true",
        help="Run only the write turn and durable memory-file/CLI checks. This keeps Docker/local-model startup smoke fast.",
    )
    parser.add_argument(
        "--direct-memory",
        action="store_true",
        help="Run only the deterministic Markdown-memory persistence smoke; do not contact OpenClaw Gateway or an LLM.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--self-test", action="store_true", help="Run local parser/scanner self-test without contacting OpenClaw.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    try:
        if args.self_test:
            result = run_self_test()
        elif args.direct_memory:
            result = run_direct_memory_smoke(
                memory_root=args.memory_root.expanduser(),
                memory_poll_s=max(0.0, args.memory_poll_s),
                memory_poll_interval_s=max(0.5, args.memory_poll_interval_s),
            )
        else:
            result = run_smoke(
                base_url=args.base_url,
                token=bearer_token_from_args(args.token),
                agent_id=args.agent_id,
                backend_model=args.backend_model,
                session_key=args.session_key,
                memory_root=args.memory_root.expanduser(),
                check_memory_files=not args.skip_memory_file_check,
                memory_poll_s=max(0.0, args.memory_poll_s),
                memory_poll_interval_s=max(0.5, args.memory_poll_interval_s),
                run_memory_cli=args.memory_cli,
                openclaw_command=args.openclaw_command,
                timeout=max(1.0, args.timeout),
                require_cross_session_recall=args.require_cross_session_recall,
                message_channel=args.message_channel,
                max_output_tokens=max(1, args.max_output_tokens),
                skip_recall_turns=args.skip_recall_turns,
            )
    except SmokeError as exc:
        result = {"ok": False, "smoke": "openclaw-persistence-surface", "error": str(exc)}
    print_result(result, as_json=args.json)
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())

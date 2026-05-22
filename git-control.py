#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SHIM_FORMAT_VERSION = "1"
SAFE_RECOMMENDATIONS = {"good", "not-recommended"}
ORDINATION_STATES = {"candidate", "ordained", "rejected"}
READ_ONLY_GIT_COMMANDS = {
    "blame",
    "branch",
    "cat-file",
    "config",
    "diff",
    "fetch",
    "grep",
    "log",
    "ls-files",
    "remote",
    "rev-list",
    "rev-parse",
    "show",
    "status",
    "tag",
}
DESTRUCTIVE_GIT_COMMANDS = {
    "am",
    "apply",
    "bisect",
    "checkout",
    "cherry-pick",
    "clean",
    "commit",
    "merge",
    "mv",
    "pull",
    "push",
    "rebase",
    "reset",
    "restore",
    "revert",
    "rm",
    "stash",
    "switch",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str, *, limit: int = 72) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    if not text:
        text = "shim"
    return text[:limit].rstrip("-") or "shim"


def stable_suffix(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8", errors="surrogateescape")).hexdigest()[:10]


def safe_join(root: Path, filename: str) -> Path:
    cleaned = str(filename or "").strip()
    if not cleaned:
        raise ValueError("Path value is required.")
    if "/" in cleaned or "\\" in cleaned or cleaned in {".", ".."} or ".." in cleaned.split("."):
        raise ValueError("Unsafe shim id.")
    return root / cleaned


def run_git(repo: Path, args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "command": ["git", *args],
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def git_available_repo_root(repo: Path) -> Path:
    result = run_git(repo, ["rev-parse", "--show-toplevel"])
    if result["returncode"] == 0 and result["stdout"].strip():
        return Path(result["stdout"].strip()).resolve()
    return repo.resolve()


def git_dir_for(repo: Path) -> Path:
    result = run_git(repo, ["rev-parse", "--git-dir"])
    if result["returncode"] == 0 and result["stdout"].strip():
        raw = Path(result["stdout"].strip())
        if not raw.is_absolute():
            raw = repo / raw
        return raw.resolve()
    return (repo / ".git").resolve()


def infer_risk(git_args: list[str]) -> str:
    if not git_args:
        return "unknown"
    command = next((item for item in git_args if item and not item.startswith("-")), "")
    if command in READ_ONLY_GIT_COMMANDS:
        return "read-only"
    if command in DESTRUCTIVE_GIT_COMMANDS:
        if command in {"reset", "clean", "push", "rebase", "filter-branch"}:
            return "history-or-data-risk"
        return "working-tree-change"
    return "unknown"


def infer_recommendation(git_args: list[str] | None, explicit: str | None = None) -> tuple[str, str]:
    value = normalize_recommendation(explicit)
    if value:
        reason = "AI or caller explicitly recommended this shim for ordination."
        if value == "not-recommended":
            reason = "AI or caller explicitly marked this shim as not recommended for ordination."
        return value, reason
    risk = infer_risk(git_args or [])
    if risk == "read-only":
        return "good", "Read-only git inspection shims are usually safe to ordain as reusable context."
    if risk == "unknown":
        return "not-recommended", "Unknown git commands should be reviewed before they are ordained."
    return "not-recommended", "Working-tree, history, or data-changing git commands need human review before ordination."


def normalize_recommendation(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower().replace("_", "-")
    if cleaned in {"", "none"}:
        return None
    if cleaned in {"good", "yes", "recommended", "ordain", "good-to-ordain"}:
        return "good"
    if cleaned in {"no", "no-recommended", "not-recommended", "not recommended", "bad", "reject", "do-not-ordain"}:
        return "not-recommended"
    if cleaned not in SAFE_RECOMMENDATIONS:
        return None
    return cleaned


def normalize_ordination_state(value: str | None) -> str:
    cleaned = str(value or "").strip().lower().replace("_", "-")
    if cleaned in {"ordained", "true", "yes"}:
        return "ordained"
    if cleaned in {"rejected", "reject", "no"}:
        return "rejected"
    return "candidate"


class GitControl:
    def __init__(self, repo: Path) -> None:
        self.repo = repo.resolve()
        self.git_root = git_available_repo_root(self.repo)
        self.git_dir = git_dir_for(self.git_root)
        self.store = self.git_dir / "git-control"
        self.shim_root = self.store / "shims"
        self.run_root = self.store / "runs"
        self.sum_root = self.store / "sums"
        for path in (self.store, self.shim_root, self.run_root, self.sum_root):
            path.mkdir(parents=True, exist_ok=True)

    def shim_path(self, shim_id: str) -> Path:
        cleaned = shim_id.removesuffix(".shim")
        path = safe_join(self.shim_root, f"{cleaned}.shim").resolve()
        try:
            path.relative_to(self.shim_root.resolve())
        except ValueError as exc:
            raise ValueError("Shim path escaped shim root.") from exc
        return path

    def create_shim(
        self,
        *,
        kind: str,
        title: str,
        git_args: list[str] | None = None,
        docs: list[str] | None = None,
        includes: list[str] | None = None,
        source: str = "git-control",
        recommendation: str | None = None,
        ordination_state: str = "candidate",
        ordination_reason: str | None = None,
        extra_meta: dict[str, str] | None = None,
        run: bool = False,
    ) -> dict[str, Any]:
        git_args = list(git_args or [])
        docs = list(docs or [])
        includes = list(includes or [])
        risk = infer_risk(git_args)
        rec, rec_reason = infer_recommendation(git_args, recommendation)
        reason = str(ordination_reason or rec_reason)
        command_text = "git " + " ".join(shlex.quote(arg) for arg in git_args) if git_args else ""
        base = f"{kind}-{title}-{command_text}-{utc_now()}"
        shim_id = f"{slugify(kind, limit=24)}-{slugify(title or command_text, limit=70)}-{stable_suffix(base)}"
        meta: dict[str, str] = {
            "git-control-shim": SHIM_FORMAT_VERSION,
            "id": shim_id,
            "kind": kind,
            "title": title or command_text or kind,
            "created-at": utc_now(),
            "source": source,
            "risk": risk,
            "ordination-state": normalize_ordination_state(ordination_state),
            "ordination-recommendation": rec,
            "ordination-reason": reason,
        }
        if command_text:
            meta["command"] = command_text
        if includes:
            meta["includes"] = ",".join(includes)
        if extra_meta:
            for key, value in extra_meta.items():
                clean_key = slugify(key, limit=48)
                meta[clean_key] = str(value)

        body: list[str] = []
        if docs:
            for item in docs:
                for line in str(item).splitlines() or [""]:
                    body.append(f"shim-doc {line}".rstrip())
        for include in includes:
            body.append(f"shim-include {include}")
        if git_args:
            body.append(command_text)
        body.append(f"shim-note ordination-recommendation {rec}: {reason}")
        text = render_shim(meta, body)
        path = self.shim_path(shim_id)
        path.write_text(text, encoding="utf-8", errors="surrogateescape")
        parsed = parse_shim_text(text, path=path)
        result: dict[str, Any] = {"ok": True, "shim": parsed, "path": str(path)}
        if run and git_args:
            result["run"] = self.run_git_command(git_args)
        return result

    def run_git_command(
        self,
        git_args: list[str],
        *,
        save_shim: bool = False,
        recommendation: str | None = None,
        ordination_reason: str | None = None,
    ) -> dict[str, Any]:
        result = run_git(self.git_root, git_args)
        payload: dict[str, Any] = {
            "ok": result["returncode"] == 0,
            "repo": str(self.git_root),
            "result": result,
        }
        if save_shim:
            title = "git " + " ".join(git_args)
            direct_kind = "git-command-read-only" if infer_risk(git_args) == "read-only" else "git-command"
            created = self.create_shim(
                kind=direct_kind,
                title=title,
                git_args=git_args,
                docs=[
                    "De novo git console command captured as a reusable shim.",
                    "This shim can be viewed, deleted, ordained, or rerun by id.",
                ],
                source="de-novo-ui-or-cli",
                recommendation=recommendation,
                ordination_reason=ordination_reason,
            )
            payload["shim"] = created["shim"]
        return payload

    def create_plan(self, prompt: str = "") -> dict[str, Any]:
        included: list[dict[str, Any]] = []
        for title, args, doc in [
            ("git status short branch", ["status", "--short", "--branch"], "Capture branch and dirty worktree state."),
            ("git recent commits", ["log", "--oneline", "-n", "8"], "Capture recent commit history for AI context."),
            ("git tracked files", ["ls-files"], "Capture tracked paths so later AI prompts can reason about repository shape."),
        ]:
            included.append(
                self.create_shim(
                    kind="git-command-read-only",
                    title=title,
                    git_args=args,
                    docs=[doc],
                    source="plan-include",
                    recommendation="good",
                    ordination_reason="Read-only inspection shim generated as plan context.",
                )["shim"]
            )

        status = self.computed_sum()
        docs = [
            "Plan mode is shim-first: the plan itself is a durable shim and includes reusable inspection shims.",
            "A human runs `python git-control.py --plan`; the UI or AI may then load ordained shims as context.",
            "Ordain only shims whose metadata and command body you want future AI calls to inherit.",
        ]
        if prompt:
            docs.append(f"Human prompt: {prompt}")
        docs.append("Computed sum:")
        docs.append(status["sum_text"])
        plan = self.create_shim(
            kind="plan-documentation",
            title="git control plan context",
            docs=docs,
            includes=[item["id"] for item in included],
            source="plan",
            recommendation="good",
            ordination_reason="Plan shims are documentation/context shims and are appropriate to ordain after human review.",
            extra_meta={"prompt": prompt} if prompt else None,
        )["shim"]
        return {
            "ok": True,
            "plan_shim": plan,
            "included_shims": included,
            "sum": status,
            "message": "Created a first-class plan shim plus included read-only inspection shims.",
        }

    def create_doc_shim(self, command_name: str, *, recommendation: str | None = None) -> dict[str, Any]:
        command = str(command_name or "").strip() or "git"
        docs = [
            f"Documentation shim for `git {command}`.",
            "Use this as durable AI context for explaining or planning git workflows.",
            "Documentation-only shims do not execute a git command unless a `git ...` directive is present.",
        ]
        rec, reason = infer_recommendation([], recommendation or "good")
        return self.create_shim(
            kind="git-doc",
            title=f"git {command} documentation",
            docs=docs,
            source="documentation",
            recommendation=rec,
            ordination_reason=reason,
        )

    def list_shims(self) -> dict[str, Any]:
        shims: list[dict[str, Any]] = []
        for path in sorted(self.shim_root.glob("*.shim"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                shims.append(parse_shim_text(path.read_text(encoding="utf-8", errors="surrogateescape"), path=path))
            except Exception as exc:
                shims.append({"id": path.stem, "ok": False, "error": str(exc), "path": str(path)})
        return {"ok": True, "root": str(self.shim_root), "shims": shims}

    def read_shim(self, shim_id: str) -> dict[str, Any]:
        path = self.shim_path(shim_id)
        if not path.exists():
            return {"ok": False, "error": f"Shim not found: {shim_id}", "shim_id": shim_id}
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        parsed = parse_shim_text(text, path=path)
        return {"ok": True, **parsed, "text": text}

    def delete_shim(self, shim_id: str) -> dict[str, Any]:
        path = self.shim_path(shim_id)
        deleted = False
        if path.exists():
            path.unlink()
            deleted = True
        return {"ok": True, "shim_id": shim_id, "deleted": deleted, "path": str(path)}

    def set_ordination(self, shim_id: str, state: str) -> dict[str, Any]:
        path = self.shim_path(shim_id)
        if not path.exists():
            return {"ok": False, "error": f"Shim not found: {shim_id}", "shim_id": shim_id}
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        parsed = parse_shim_text(text, path=path)
        metadata = dict(parsed.get("metadata") or {})
        normalized = normalize_ordination_state(state)
        metadata["ordination-state"] = normalized
        if normalized == "ordained":
            metadata["ordained-at"] = utc_now()
            if metadata.get("ordination-recommendation") != "good":
                metadata.setdefault(
                    "ordination-warning",
                    "User ordained this shim even though its recommendation is not `good`.",
                )
        body = parsed.get("body_lines") or []
        path.write_text(render_shim(metadata, body), encoding="utf-8", errors="surrogateescape")
        reread = self.read_shim(shim_id)
        return {"ok": bool(reread.get("ok")), "shim": {key: value for key, value in reread.items() if key != "text"}}

    def run_shim(self, shim_id: str) -> dict[str, Any]:
        read = self.read_shim(shim_id)
        if not read.get("ok"):
            return read
        results = []
        for command in read.get("git_commands", []):
            args = shlex.split(command, posix=os.name != "nt")
            if args and args[0] == "git":
                args = args[1:]
            result = run_git(self.git_root, args)
            results.append({"command": command, "result": result})
        ok = all(item["result"]["returncode"] == 0 for item in results) if results else True
        record = {
            "ok": ok,
            "shim_id": shim_id,
            "ran_at": utc_now(),
            "results": results,
        }
        run_path = self.run_root / f"{slugify(shim_id, limit=80)}-{stable_suffix(json.dumps(record, sort_keys=True))}.json"
        run_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return {"ok": ok, "shim_id": shim_id, "results": results, "run_record": str(run_path)}

    def computed_sum(self) -> dict[str, Any]:
        status = run_git(self.git_root, ["status", "--short", "--branch"])
        branch = run_git(self.git_root, ["branch", "--show-current"])
        recent = run_git(self.git_root, ["log", "--oneline", "-n", "5"])
        ordained = self.ordained_context(limit=8, include_text=False)
        lines = [
            f"repo: {self.git_root}",
            f"branch: {branch['stdout'].strip() or 'detached-or-unknown'}",
            "status:",
            status["stdout"].strip() or "(clean or unavailable)",
            "recent commits:",
            recent["stdout"].strip() or "(no commits or unavailable)",
            f"ordained shims: {len(ordained['shims'])}",
        ]
        sum_text = "\n".join(lines)
        payload = {
            "ok": True,
            "repo": str(self.git_root),
            "git_dir": str(self.git_dir),
            "sum_text": sum_text,
            "status": status,
            "branch": branch,
            "recent": recent,
            "ordained_count": len(ordained["shims"]),
        }
        path = self.sum_root / "latest.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def ordained_context(self, *, limit: int = 12, include_text: bool = True) -> dict[str, Any]:
        listing = self.list_shims()
        ordained = [
            shim
            for shim in listing.get("shims", [])
            if normalize_ordination_state((shim.get("metadata") or {}).get("ordination-state")) == "ordained"
        ][: max(0, int(limit))]
        chunks: list[str] = []
        for shim in ordained:
            path = Path(str(shim.get("path") or ""))
            if include_text and path.exists():
                body = path.read_text(encoding="utf-8", errors="surrogateescape")
            else:
                body = summarize_shim_for_context(shim)
            chunks.append(body.strip())
        return {
            "ok": True,
            "count": len(ordained),
            "shims": ordained,
            "context": "\n\n--- ordained git-control shim ---\n\n".join(chunks),
        }

    def ai_brief(self, prompt: str = "") -> dict[str, Any]:
        computed = self.computed_sum()
        ordained = self.ordained_context(limit=12, include_text=True)
        instruction = str(prompt or "").strip() or "Recommend the next safe git-control shim for this repository."
        system = f"""You are helping a human operate git through git-control.py.

The user has ordained some git-control shims. Treat ordained shims as durable local policy/context.
When you recommend a new shim, include parseable metadata and an ordination recommendation.

Use this exact shim block shape when proposing a shim:

```shim
# git-control-shim: 1
# title: short human title
# kind: git-command
# ordination-recommendation: good
# ordination-reason: why this is good to ordain, or why it is not recommended
shim-doc what this command does
git status --short --branch
```

Use `# ordination-recommendation: good` only for shims that are safe and broadly useful to ordain.
Use `# ordination-recommendation: not-recommended` for destructive, risky, one-off, or context-specific commands.
You may also show runnable Python command lines such as:
python git-control.py --recommend good --git status --short --branch

Do not ask the user to run raw shell commands when git-control.py can represent the command as a shim.
"""
        full_prompt = "\n\n".join(
            [
                system,
                "Current computed git sum:",
                computed["sum_text"],
                "Ordained shim context:",
                ordained["context"] or "(no shims ordained yet)",
                "Human request:",
                instruction,
            ]
        )
        return {
            "ok": True,
            "prompt": full_prompt,
            "computed_sum": computed,
            "ordained_context": ordained,
        }

    def extract_shims_from_text(self, text: str, *, source: str = "ai-output") -> dict[str, Any]:
        candidates = extract_shim_blocks(text)
        commands = extract_git_control_commands(text)
        shims: list[dict[str, Any]] = []

        for block in candidates:
            created = self.create_shim_from_block(block, source=source)
            shims.append(created["shim"])

        for command_args in commands:
            created = self.create_shim_from_git_control_args(command_args, source=source)
            if created:
                shims.append(created["shim"])

        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for shim in shims:
            key = "\n".join(shim.get("git_commands") or []) or shim.get("id", "")
            if key in seen:
                continue
            seen.add(key)
            unique.append(shim)

        return {"ok": True, "command_count": len(commands), "shim_block_count": len(candidates), "shims": unique}

    def create_shim_from_block(self, block: str, *, source: str) -> dict[str, Any]:
        parsed = parse_shim_text(block)
        meta = parsed.get("metadata") or {}
        git_commands = parsed.get("git_commands") or []
        docs = [
            line.removeprefix("shim-doc").strip()
            for line in parsed.get("body_lines", [])
            if str(line).strip().startswith("shim-doc")
        ]
        includes = parsed.get("includes") or []
        title = str(meta.get("title") or (git_commands[0] if git_commands else "AI generated shim"))
        kind = str(meta.get("kind") or ("git-command" if git_commands else "git-doc"))
        recommendation = normalize_recommendation(str(meta.get("ordination-recommendation") or ""))
        reason = str(meta.get("ordination-reason") or "")
        git_args: list[str] = []
        if git_commands:
            args = shlex.split(git_commands[0], posix=os.name != "nt")
            git_args = args[1:] if args and args[0] == "git" else args
        return self.create_shim(
            kind=kind,
            title=title,
            git_args=git_args,
            docs=docs or ["AI generated shim candidate extracted from shim-code output."],
            includes=includes,
            source=source,
            recommendation=recommendation,
            ordination_state=normalize_ordination_state(str(meta.get("ordination-state") or "candidate")),
            ordination_reason=reason or None,
            extra_meta={k: v for k, v in meta.items() if k not in {"id", "kind", "title", "git-control-shim", "command"}},
        )

    def create_shim_from_git_control_args(self, args: list[str], *, source: str) -> dict[str, Any] | None:
        recommendation: str | None = None
        ordination_reason: str | None = None
        cleaned: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token == "--recommend" and index + 1 < len(args):
                recommendation = args[index + 1]
                index += 2
                continue
            if token == "--ordination-reason" and index + 1 < len(args):
                ordination_reason = args[index + 1]
                index += 2
                continue
            if token == "--json":
                index += 1
                continue
            cleaned.append(token)
            index += 1

        if "--git" in cleaned:
            git_index = cleaned.index("--git")
            git_args = cleaned[git_index + 1 :]
            if not git_args:
                return None
            return self.create_shim(
                kind="git-command",
                title="git " + " ".join(git_args),
                git_args=git_args,
                docs=["AI proposed this git-control.py command. It was saved without running."],
                source=source,
                recommendation=recommendation,
                ordination_reason=ordination_reason,
            )
        if "--doc-shim" in cleaned:
            doc_index = cleaned.index("--doc-shim")
            command = cleaned[doc_index + 1] if doc_index + 1 < len(cleaned) else "git"
            return self.create_doc_shim(command, recommendation=recommendation)
        if "--plan" in cleaned:
            return self.create_shim(
                kind="plan-documentation",
                title="ai proposed git-control plan",
                docs=["AI proposed `python git-control.py --plan`; saved as a documentation shim without running it."],
                source=source,
                recommendation=recommendation or "good",
                ordination_reason=ordination_reason or "Plan shims are context-only until the human runs them.",
            )
        return None


def render_shim(metadata: dict[str, str], body_lines: list[str]) -> str:
    ordered_keys = [
        "git-control-shim",
        "id",
        "kind",
        "title",
        "created-at",
        "source",
        "risk",
        "ordination-state",
        "ordination-recommendation",
        "ordination-reason",
        "ordained-at",
        "ordination-warning",
        "command",
        "includes",
        "prompt",
    ]
    lines: list[str] = []
    emitted: set[str] = set()
    for key in ordered_keys:
        if key in metadata and metadata[key] not in {None, ""}:
            lines.append(f"# {key}: {metadata[key]}")
            emitted.add(key)
    for key in sorted(metadata):
        if key not in emitted and metadata[key] not in {None, ""}:
            lines.append(f"# {key}: {metadata[key]}")
    lines.append("")
    lines.extend(str(line).rstrip() for line in body_lines)
    lines.append("")
    return "\n".join(lines)


def parse_shim_text(text: str, *, path: Path | None = None) -> dict[str, Any]:
    metadata: dict[str, str] = {}
    body_lines: list[str] = []
    git_commands: list[str] = []
    includes: list[str] = []

    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("#"):
            content = stripped[1:].strip()
            if ":" in content:
                key, value = content.split(":", 1)
                key = key.strip().lower().replace("_", "-")
                value = value.strip()
                if key == "ordination-recommendation":
                    value = normalize_recommendation(value) or value
                metadata[key] = value
            continue
        if not stripped:
            body_lines.append(line)
            continue
        body_lines.append(line)
        if stripped.startswith("shim-include "):
            includes.append(stripped.split(None, 1)[1].strip())
        elif stripped.startswith("git "):
            git_commands.append(stripped)

    if "command" in metadata and str(metadata["command"]).startswith("git "):
        if metadata["command"] not in git_commands:
            git_commands.insert(0, metadata["command"])
    if "includes" in metadata:
        for include in str(metadata["includes"]).split(","):
            include = include.strip()
            if include and include not in includes:
                includes.append(include)

    shim_id = metadata.get("id") or (path.stem if path else "")
    return {
        "ok": True,
        "id": shim_id,
        "kind": metadata.get("kind", "shim"),
        "title": metadata.get("title", shim_id),
        "risk": metadata.get("risk", "unknown"),
        "ordination_state": normalize_ordination_state(metadata.get("ordination-state")),
        "ordination_recommendation": normalize_recommendation(metadata.get("ordination-recommendation")) or "not-recommended",
        "ordination_reason": metadata.get("ordination-reason", ""),
        "ordained": normalize_ordination_state(metadata.get("ordination-state")) == "ordained",
        "metadata": metadata,
        "git_commands": git_commands,
        "includes": includes,
        "body_lines": body_lines,
        "path": str(path) if path else "",
    }


def summarize_shim_for_context(shim: dict[str, Any]) -> str:
    meta = shim.get("metadata") or {}
    commands = "\n".join(shim.get("git_commands") or [])
    return "\n".join(
        [
            f"# id: {shim.get('id', '')}",
            f"# title: {shim.get('title', '')}",
            f"# kind: {shim.get('kind', '')}",
            f"# ordination-recommendation: {shim.get('ordination_recommendation', meta.get('ordination-recommendation', ''))}",
            f"# ordination-reason: {shim.get('ordination_reason', meta.get('ordination-reason', ''))}",
            commands,
        ]
    ).strip()


def extract_shim_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"```(?:shim|git-control-shim)\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL):
        body = match.group(1).strip()
        if body:
            blocks.append(body)
    return blocks


def extract_git_control_commands(text: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for line in str(text or "").splitlines():
        stripped = line.strip().strip("`")
        if not stripped or "git-control.py" not in stripped:
            continue
        if stripped.startswith("#"):
            continue
        try:
            parts = shlex.split(stripped, posix=os.name != "nt")
        except ValueError:
            parts = stripped.split()
        if len(parts) >= 2 and Path(parts[1].replace("\\", "/")).name == "git-control.py":
            commands.append(parts[2:])
            continue

    for match in re.finditer(r"\[(.*?)\]", str(text or ""), flags=re.DOTALL):
        raw = match.group(1)
        if "git-control.py" not in raw:
            continue
        items = re.findall(r"""['"]([^'"]+)['"]""", raw)
        if "git-control.py" in [Path(item.replace("\\", "/")).name for item in items]:
            try:
                script_index = [Path(item.replace("\\", "/")).name for item in items].index("git-control.py")
            except ValueError:
                continue
            commands.append(items[script_index + 1 :])
    return commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shim-first git control helper.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--repo", default=".", help="Repository root or directory inside a repository.")
    parser.add_argument("--recommend", choices=sorted(SAFE_RECOMMENDATIONS), help="Ordination recommendation for generated shim metadata.")
    parser.add_argument("--ordination-reason", default="", help="Human/AI reason for the ordination recommendation.")
    parser.add_argument("--plan", action="store_true", help="Create a first-class plan shim.")
    parser.add_argument("--prompt", default="", help="Prompt or instruction for plan/AI brief.")
    parser.add_argument("--git", nargs=argparse.REMAINDER, help="Run git with arbitrary arguments and save a shim.")
    parser.add_argument("--doc-shim", default=None, help="Create a documentation-only shim for a git command.")
    parser.add_argument("--list-shims", action="store_true", help="List stored shims.")
    parser.add_argument("--read-shim", default=None, help="Read a stored shim.")
    parser.add_argument("--run-shim", default=None, help="Rerun git commands from a stored shim.")
    parser.add_argument("--delete-shim", default=None, help="Delete a stored shim.")
    parser.add_argument("--ordain-shim", default=None, help="Mark a shim as ordained so future AI prompts load it.")
    parser.add_argument("--unordain-shim", default=None, help="Return a shim to candidate state.")
    parser.add_argument("--ordained-context", action="store_true", help="Print ordained shim context.")
    parser.add_argument("--sum", action="store_true", help="Compute current git sum.")
    parser.add_argument("--ai-brief", action="store_true", help="Build the AI prompt that loads ordained shims.")
    parser.add_argument("--extract-shims-from", default=None, help="Extract git-control commands or shim blocks from AI output.")
    args = parser.parse_args(argv)

    control = GitControl(Path(args.repo))
    try:
        if args.plan:
            payload = control.create_plan(args.prompt)
        elif args.git is not None:
            if not args.git:
                raise ValueError("--git requires git arguments.")
            payload = control.run_git_command(
                args.git,
                save_shim=True,
                recommendation=args.recommend,
                ordination_reason=args.ordination_reason or None,
            )
        elif args.doc_shim is not None:
            payload = control.create_doc_shim(args.doc_shim, recommendation=args.recommend)
        elif args.list_shims:
            payload = control.list_shims()
        elif args.read_shim:
            payload = control.read_shim(args.read_shim)
        elif args.run_shim:
            payload = control.run_shim(args.run_shim)
        elif args.delete_shim:
            payload = control.delete_shim(args.delete_shim)
        elif args.ordain_shim:
            payload = control.set_ordination(args.ordain_shim, "ordained")
        elif args.unordain_shim:
            payload = control.set_ordination(args.unordain_shim, "candidate")
        elif args.ordained_context:
            payload = control.ordained_context()
        elif args.sum:
            payload = control.computed_sum()
        elif args.ai_brief:
            payload = control.ai_brief(args.prompt)
        elif args.extract_shims_from:
            source_path = Path(args.extract_shims_from)
            text = source_path.read_text(encoding="utf-8", errors="surrogateescape")
            payload = control.extract_shims_from_text(text, source=f"ai-output:{source_path.name}")
        else:
            payload = control.create_plan(args.prompt)
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"git-control.py: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path as RealPath


class CapabilityViolation(RuntimeError):
    pass


@dataclass
class LoadedFile:
    file_id: str
    repo_path: str
    real_path: RealPath
    original_text: str
    current_text: str
    reads: int = 0
    writes: int = 0


class CapabilityFS:
    def __init__(self, repo_root_name: str) -> None:
        self.repo_root_name = repo_root_name.replace("\\", "/").strip("/")
        self.loaded: dict[str, LoadedFile] = {}
        self.events: list[dict[str, str]] = []

    def load(self, file_id: str, repo_path: str, real_path: RealPath) -> None:
        repo_path = self.clean_loaded_path(repo_path)
        text = real_path.read_text(encoding="utf-8")
        self.loaded[repo_path] = LoadedFile(file_id, repo_path, real_path, text, text)

    def Path(self, *parts: object) -> "FakePath":
        return FakePath(self, "/".join(str(p) for p in parts if str(p)))

    def clean_loaded_path(self, raw: str) -> str:
        parts = [p for p in str(raw).replace("\\", "/").split("/") if p and p != "."]
        if not parts or ".." in parts:
            raise CapabilityViolation(f"loaded path is not repo-relative: {raw}")
        return "/".join(parts)

    def resolve(self, raw: str) -> LoadedFile:
        raw = str(raw).replace("\\", "/").strip().replace("://", ":/")
        parts = [p for p in raw.split("/") if p and p != "."]
        if not parts:
            raise CapabilityViolation("empty path")

        if self.repo_root_name in parts:
            tail = parts[parts.index(self.repo_root_name) + 1 :]
            if not tail:
                raise CapabilityViolation(f"path resolves only to repo root: {raw}")
            if ".." in tail:
                raise CapabilityViolation(f"path traversal after repo root is not authorized: {raw}")
            candidate = "/".join(tail)
        else:
            if ".." in parts:
                raise CapabilityViolation(f"path traversal is not authorized: {raw}")
            candidate = "/".join(parts)

        if candidate not in self.loaded:
            raise CapabilityViolation(f"path is not loaded: {raw}")
        return self.loaded[candidate]

    def exists(self, raw: str) -> bool:
        try:
            self.resolve(raw)
            return True
        except CapabilityViolation:
            return False

    def read_text(self, raw: str) -> str:
        f = self.resolve(raw)
        f.reads += 1
        self.events.append({"op": "read", "file_id": f.file_id, "path": f.repo_path})
        return f.current_text

    def write_text(self, raw: str, text: str) -> int:
        f = self.resolve(raw)
        f.current_text = str(text)
        f.writes += 1
        self.events.append({"op": "write", "file_id": f.file_id, "path": f.repo_path})
        return len(str(text))

    def diffs(self) -> list[dict[str, object]]:
        out = []
        for f in self.loaded.values():
            if f.current_text != f.original_text:
                out.append({
                    "file_id": f.file_id,
                    "repo_path": f.repo_path,
                    "original_sha256": hashlib.sha256(f.original_text.encode("utf-8")).hexdigest(),
                    "proposed_sha256": hashlib.sha256(f.current_text.encode("utf-8")).hexdigest(),
                    "reads": f.reads,
                    "writes": f.writes,
                })
        return out


class FakePath:
    def __init__(self, fs: CapabilityFS, raw: str) -> None:
        self.fs = fs
        self.raw = raw

    def __truediv__(self, other: object) -> "FakePath":
        return FakePath(self.fs, self.raw.rstrip("/\\") + "/" + str(other).strip("/\\"))

    def exists(self) -> bool:
        return self.fs.exists(self.raw)

    def read_text(self, *args: object, **kwargs: object) -> str:
        return self.fs.read_text(self.raw)

    def write_text(self, text: str, *args: object, **kwargs: object) -> int:
        return self.fs.write_text(self.raw, text)

    def __fspath__(self) -> str:
        return self.raw

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"FakePath({self.raw!r})"


@contextlib.contextmanager
def patched_pathlib(fs: CapabilityFS):
    old = sys.modules.get("pathlib")
    fake = types.ModuleType("pathlib")
    fake.Path = fs.Path
    sys.modules["pathlib"] = fake
    try:
        yield
    finally:
        if old is None:
            sys.modules.pop("pathlib", None)
        else:
            sys.modules["pathlib"] = old


def run_generated(source: str, fs: CapabilityFS) -> None:
    ns: dict[str, object] = {"__name__": "generated_editor"}
    with patched_pathlib(fs):
        exec(compile(source, "<generated-editor>", "exec"), ns, ns)
        main = ns.get("main")
        if not callable(main):
            raise AssertionError("generated editor did not define callable main()")
        main()


def repo_root_from_cwd() -> RealPath:
    here = RealPath.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "main_computer").is_dir():
            return p
    raise SystemExit("run from the repository root or a child directory")


def new_fs(repo_root: RealPath, repo_root_name: str, repo_path: str) -> CapabilityFS:
    fs = CapabilityFS(repo_root_name)
    fs.load("f_chat_console_js", repo_path, repo_root / repo_path)
    return fs


def generated_replace(path_expr: str, old: str, new: str) -> str:
    return f"""
from pathlib import Path

def main():
    target = {path_expr}
    text = target.read_text()
    old = {old!r}
    new = {new!r}
    if old not in text:
        raise RuntimeError("gr_miss")
    target.write_text(text.replace(old, new, 1))
"""


def test_loaded_repo_prefixed_path(repo_root: RealPath, repo_root_name: str, repo_path: str) -> dict[str, object]:
    real = repo_root / repo_path
    original = real.read_text(encoding="utf-8")
    old = "      button.textContent = label;"
    new = "      button.textContent = String(label);"
    if old not in original:
        raise AssertionError(f"fixture anchor missing in {repo_path}")

    fs = new_fs(repo_root, repo_root_name, repo_path)
    run_generated(generated_replace(f"Path({repo_root_name!r}) / {repo_path!r}", old, new), fs)

    if real.read_text(encoding="utf-8") != original:
        raise AssertionError("real file was modified")
    proposed = fs.loaded[repo_path].current_text
    if proposed.count(old) != original.count(old) - 1 or proposed.count(new) != original.count(new) + 1:
        raise AssertionError("transaction buffer was not edited exactly once")
    return {"name": "loaded_repo_prefixed_path", "ok": True, "events": fs.events, "diffs": fs.diffs()}


def test_windows_absolute_path(repo_root: RealPath, repo_root_name: str, repo_path: str) -> dict[str, object]:
    real = repo_root / repo_path
    original = real.read_text(encoding="utf-8")
    old = "      button.type = \"button\";"
    new = "      button.type = \"button\";\n      button.dataset.capabilitySmoke = \"1\";"
    raw = str((repo_root / repo_path).resolve())

    fs = new_fs(repo_root, repo_root_name, repo_path)
    run_generated(generated_replace(f"Path({raw!r})", old, new), fs)

    if real.read_text(encoding="utf-8") != original:
        raise AssertionError("real file was modified")
    if new not in fs.loaded[repo_path].current_text:
        raise AssertionError("absolute path was not reduced into the loaded transaction")
    return {"name": "windows_absolute_path", "ok": True, "events": fs.events, "diffs": fs.diffs()}


def expect_blocked(name: str, source: str, repo_root: RealPath, repo_root_name: str, repo_path: str) -> dict[str, object]:
    fs = new_fs(repo_root, repo_root_name, repo_path)
    try:
        run_generated(source, fs)
    except CapabilityViolation as exc:
        return {"name": name, "ok": True, "error": str(exc)}
    raise AssertionError(f"{name} was not blocked")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root-name")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo_root = repo_root_from_cwd()
    repo_root_name = args.repo_root_name or repo_root.name
    repo_path = "main_computer/web/applications/scripts/chat-console.js"
    if not (repo_root / repo_path).exists():
        raise SystemExit(f"fixture file missing: {repo_path}")

    results = [
        test_loaded_repo_prefixed_path(repo_root, repo_root_name, repo_path),
        test_windows_absolute_path(repo_root, repo_root_name, repo_path),
        expect_blocked(
            "unloaded_path_rejected",
            'from pathlib import Path\n\ndef main():\n    Path("main_computer/web/applications/scripts/not-loaded.js").write_text("x")\n',
            repo_root,
            repo_root_name,
            repo_path,
        ),
        expect_blocked(
            "shortened_basename_rejected",
            'from pathlib import Path\n\ndef main():\n    Path("chat-console.js").read_text()\n',
            repo_root,
            repo_root_name,
            repo_path,
        ),
        expect_blocked(
            "traversal_rejected",
            f'from pathlib import Path\n\ndef main():\n    (Path({repo_root_name!r}) / ".." / "secrets.txt").write_text("x")\n',
            repo_root,
            repo_root_name,
            repo_path,
        ),
    ]

    payload = {"ok": True, "repo_root": str(repo_root), "repo_root_name": repo_root_name, "results": results}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"[done] capability filesystem smoke passed for {repo_root_name}")
        for r in results:
            print(f"[ok] {r['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

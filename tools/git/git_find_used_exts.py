# ext_inventory.py
from __future__ import annotations

import json
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

def git_paths() -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return [p for p in out.splitlines() if p.strip()]
    except Exception:
        paths = []
        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                paths.append(str(Path(root, name).as_posix()).lstrip("./"))
        return paths

def ext_of(path: str) -> str:
    name = Path(path).name
    if "." not in name:
        return "[no extension]"
    if name.startswith(".") and name.count(".") == 1:
        return "[dotfile]"
    return Path(path).suffix.lower() or "[no extension]"

def main() -> None:
    paths = git_paths()
    counts = Counter()
    examples = defaultdict(list)

    for p in paths:
        ext = ext_of(p)
        counts[ext] += 1
        if len(examples[ext]) < 8:
            examples[ext].append(p)

    report = {
        "total_files": len(paths),
        "extensions": [
            {
                "extension": ext,
                "count": count,
                "examples": examples[ext],
            }
            for ext, count in counts.most_common()
        ],
    }

    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
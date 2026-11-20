"""Run the non-promoting MCEL source-authority shadow.

Example:
    python -m tools.mcel_source_authority_shadow --app code-editor \\
      --output runtime/reports/mcel-source-authority-shadow/code-editor.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from main_computer.mcel_source_authority_shadow import ROOT, evaluate_source_authority
from main_computer.mcel_semantic_other_apps_host import APPS


def _report_destination(repo: Path, requested: str) -> Path:
    """Only explicitly requested reports under runtime/reports can be written."""
    destination = Path(requested)
    if not destination.is_absolute():
        destination = repo / destination
    destination = destination.resolve()
    reports = (repo / "runtime" / "reports").resolve()
    if not destination.is_relative_to(reports) or destination == reports or destination.suffix != ".json":
        raise ValueError("--output must be a JSON file under this repository's runtime/reports/")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile and compare MCEL sources without promoting or routing the result")
    parser.add_argument("--app", choices=sorted(APPS), default="code-editor")
    parser.add_argument("--repo", type=Path, default=ROOT, help="Source repository to observe")
    parser.add_argument("--output", help="Optional JSON report under runtime/reports/; stdout otherwise")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    if not (repo / "main_computer" / "mcel_semantic_other_apps_host.py").is_file():
        parser.error("--repo is not an MCEL repository")
    report = evaluate_source_authority(args.app, repo)
    data = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        try:
            path = _report_destination(repo, args.output)
        except ValueError as exc:
            parser.error(str(exc))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".mcel-shadow-", suffix=".tmp", delete=False) as stream:
                temp = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        print(f"MCEL_SOURCE_AUTHORITY_SHADOW app={args.app} outcome={report['outcome']} "
              f"promotion=REFUSED report={path}")
    else:
        print(data, end="")
    # Shadow mismatches are measured outcomes, not tool execution errors.
    # Incomplete compile/host errors remain explicit blockers in the report.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

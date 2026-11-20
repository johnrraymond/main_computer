"""Patch 4A: read-only Code Editor vs VS Code semantic workspace audit.

python -m tools.mcel_code_editor_reconciliation_audit --output \
    runtime/reports/mcel-code-editor-reconciliation/code-editor-4a.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from main_computer.mcel_code_editor_reconciliation_audit import evaluate_reconciliation, ROOT
from tools.mcel_source_authority_shadow import _report_destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Non-promoting Code Editor semantic and VS Code workspace audit")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", help="JSON output, confined to runtime/reports; stdout by default")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    if not (repo / "main_computer/mcel_semantic_other_apps_host.py").is_file():
        parser.error("--repo must be a recognized MCEL repository")
    result = evaluate_reconciliation(repo)
    encoded = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output:
        try:
            path = _report_destination(repo, args.output)
        except ValueError as exc:
            parser.error(str(exc))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".mcel-4a-", suffix=".tmp", delete=False) as stream:
                tmp = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        print("MCEL_CODE_EDITOR_4A outcome=RECONCILIATION_INCOMPLETE promotion=REFUSED "
              f"mapped={result['functionalReconciliation']['bridgeMappedIncumbentIntents']} "
              f"additional={result['functionalReconciliation']['incumbentAdditionalIntents']} report={path}")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Create the all-father-only private state file from the main private state.

The all-father deploy path needs Coolify connection/project/server bindings, but
it should not depend on the larger ``runtime/state/main_computer.private.yaml``
file.  This tool copies only the Coolify section into
``runtime/state/all_father.private.yaml`` so guarded all-father commands can use a
smaller, purpose-built private state source.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PATH = REPO_ROOT / "runtime" / "state" / "main_computer.private.yaml"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "runtime" / "state" / "all_father.private.yaml"
ALLFATHER_PRIVATE_KIND = "main_computer.all_father.private_state.v1"
COPIED_SECTIONS = ("coolify",)


class AllfatherPrivateStateError(ValueError):
    """Raised when the all-father private state cannot be initialized safely."""


def repo_relative_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is part of the project test env.
        raise AllfatherPrivateStateError("PyYAML is required to read private state YAML.") from exc

    if not path.exists():
        raise AllfatherPrivateStateError(f"Source private state file does not exist: {display_path(path)}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AllfatherPrivateStateError(f"Could not read source private state file {display_path(path)}: {exc}") from exc
    except Exception as exc:
        raise AllfatherPrivateStateError(f"Could not parse source private state YAML {display_path(path)}: {exc}") from exc

    if not isinstance(loaded, dict):
        raise AllfatherPrivateStateError(f"Source private state file must contain a YAML mapping: {display_path(path)}")
    return loaded


def dump_yaml_mapping(state: Mapping[str, Any]) -> str:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is part of the project test env.
        raise AllfatherPrivateStateError("PyYAML is required to write private state YAML.") from exc

    return yaml.safe_dump(dict(state), sort_keys=False, allow_unicode=True)


def build_allfather_private_state(source_state: Mapping[str, Any], *, source_path: str = "") -> dict[str, Any]:
    coolify = source_state.get("coolify")
    if not isinstance(coolify, Mapping):
        raise AllfatherPrivateStateError("Source private state does not contain a top-level coolify mapping.")

    migrated: dict[str, Any] = {
        "schema_version": 1,
        "kind": ALLFATHER_PRIVATE_KIND,
        "generated_from": {
            "path": source_path,
            "copied_sections": list(COPIED_SECTIONS),
        },
        "coolify": copy.deepcopy(dict(coolify)),
    }
    return migrated


def migration_summary(source_state: Mapping[str, Any], migrated: Mapping[str, Any], *, source: Path, out: Path) -> dict[str, Any]:
    coolify = migrated.get("coolify") if isinstance(migrated, Mapping) else {}
    hosts = coolify.get("hosts") if isinstance(coolify, Mapping) else {}
    omitted = sorted(str(key) for key in source_state.keys() if key not in set(COPIED_SECTIONS) | {"schema_version"})

    return {
        "kind": ALLFATHER_PRIVATE_KIND,
        "source": display_path(source),
        "out": display_path(out),
        "copied_sections": list(COPIED_SECTIONS),
        "omitted_sections": omitted,
        "coolify_host_count": len(hosts) if isinstance(hosts, Mapping) else 0,
    }


def migrate_coolify_private_state(
    *,
    source: Path = DEFAULT_SOURCE_PATH,
    out: Path = DEFAULT_OUTPUT_PATH,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    source = repo_relative_path(source)
    out = repo_relative_path(out)
    source_state = load_yaml_mapping(source)
    migrated = build_allfather_private_state(source_state, source_path=display_path(source))
    rendered = dump_yaml_mapping(migrated)
    summary = migration_summary(source_state, migrated, source=source, out=out)

    if dry_run:
        return {"ok": True, "dry_run": True, **summary, "yaml": rendered}

    if out.exists() and not force:
        raise AllfatherPrivateStateError(
            f"Refusing to overwrite existing all-father private state file: {display_path(out)}. "
            "Pass --force after reviewing --dry-run output."
        )

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise AllfatherPrivateStateError(f"Could not write all-father private state file {display_path(out)}: {exc}") from exc

    return {"ok": True, "dry_run": False, "written": display_path(out), **summary}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize the all-father-specific private state file.",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser(
        "migrate-coolify",
        help="Copy only the Coolify section from main_computer.private.yaml into all_father.private.yaml.",
    )
    migrate.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH, help="Existing main private state YAML.")
    migrate.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_PATH, help="All-father private state YAML to create.")
    migrate.add_argument("--dry-run", action="store_true", help="Print the generated YAML in JSON output without writing.")
    migrate.add_argument("--force", action="store_true", help="Overwrite an existing --out file.")

    return parser.parse_args(argv)


def _print_result(result: Mapping[str, Any]) -> None:
    print(json.dumps(dict(result), indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "migrate-coolify":
            result = migrate_coolify_private_state(
                source=args.source,
                out=args.out,
                force=bool(args.force),
                dry_run=bool(args.dry_run),
            )
            _print_result(result)
            return 0
        raise AllfatherPrivateStateError(f"Unhandled command {args.command!r}.")
    except AllfatherPrivateStateError as exc:
        _print_result({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

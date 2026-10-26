#!/usr/bin/env python3
"""Conservatively retire old NanoJev run directories, preserving CLEF champion.

For the October 25, 2026 cleanup, the fixed retention cutoff is October 22
at 00:00 *local time*. Preview is the default; deletion requires two flags.
Nothing under NanoJev/releases is ever in scope.

    python -m tools.nanojev_runs_cleanup
    python -m tools.nanojev_runs_cleanup --apply --confirm DELETE_OLD_RUNS
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys

CHAMPION_RUN = "three_backbone_clef_tinystories_structured_supervision_train_v1"
DEFAULT_SINCE = "2026-10-22"
CONFIRMATION = "DELETE_OLD_RUNS"


class CleanupRefused(RuntimeError):
    """Refuse deletion when provenance or filesystem safety is uncertain."""


@dataclass(frozen=True)
class Snapshot:
    path: Path
    kind: str
    bytes: int
    files: int
    newest_ns: int
    fingerprint: str
    unsafe: bool = False


@dataclass(frozen=True)
class CleanupPlan:
    root: Path
    cutoff: datetime
    champion: Path
    retained: tuple[Snapshot, ...]
    candidates: tuple[Snapshot, ...]

    @property
    def reclaim_bytes(self) -> int:
        return sum(s.bytes for s in self.candidates)


def _is_reparse(path: Path, record: os.stat_result) -> bool:
    if stat.S_ISLNK(record.st_mode):
        return True
    attributes = getattr(record, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def snapshot(path: Path) -> Snapshot:
    """Read all descendant timestamps; never follow links/junctions.

    Fingerprints cover relative names, kinds, mtimes, and byte lengths. They
    detect changes between planning and deletion without reading huge models.
    """
    try:
        top = path.lstat()
    except OSError as exc:
        raise CleanupRefused(f"Cannot stat {path}: {exc}") from exc
    if _is_reparse(path, top):
        return Snapshot(path, "unsafe", 0, 0, top.st_mtime_ns, "", True)
    if stat.S_ISREG(top.st_mode):
        digest = hashlib.sha256()
        digest.update(f"file|{path.name}|{top.st_size}|{top.st_mtime_ns}".encode())
        return Snapshot(path, "file", top.st_size, 1, top.st_mtime_ns, digest.hexdigest())
    if not stat.S_ISDIR(top.st_mode):
        return Snapshot(path, "unsafe", 0, 0, top.st_mtime_ns, "", True)

    digest = hashlib.sha256()
    total_bytes = 0
    file_count = 0
    newest_ns = top.st_mtime_ns
    stack = [(path, "")]
    while stack:
        folder, rel = stack.pop()
        try:
            folder_st = folder.lstat()
            if _is_reparse(folder, folder_st) or not stat.S_ISDIR(folder_st.st_mode):
                return Snapshot(path, "unsafe", 0, 0, newest_ns, "", True)
            digest.update(f"D|{rel}|{folder_st.st_mtime_ns}\n".encode("utf-8", "surrogatepass"))
            newest_ns = max(newest_ns, folder_st.st_mtime_ns)
            with os.scandir(folder) as it:
                children = sorted(it, key=lambda e: e.name)
            for child in children:
                child_path = Path(child.path)
                child_rel = f"{rel}/{child.name}" if rel else child.name
                record = child_path.lstat()
                newest_ns = max(newest_ns, record.st_mtime_ns)
                if _is_reparse(child_path, record):
                    return Snapshot(path, "unsafe", 0, 0, newest_ns, "", True)
                if stat.S_ISDIR(record.st_mode):
                    stack.append((child_path, child_rel))
                elif stat.S_ISREG(record.st_mode):
                    digest.update(f"F|{child_rel}|{record.st_size}|{record.st_mtime_ns}\n".encode("utf-8", "surrogatepass"))
                    total_bytes += record.st_size
                    file_count += 1
                else:
                    return Snapshot(path, "unsafe", 0, 0, newest_ns, "", True)
        except OSError as exc:
            raise CleanupRefused(f"Cannot inspect {folder}: {exc}") from exc
    return Snapshot(path, "directory", total_bytes, file_count, newest_ns, digest.hexdigest())


def validate_champion(root: Path) -> Path:
    """Use the repository's authoritative CLEF best-checkpoint resolver."""
    try:
        from tools.nanojev_clef_publish import resolve_checkpoint
    except ImportError as exc:
        raise CleanupRefused("Repository CLEF champion resolver is unavailable") from exc

    experiment = root / CHAMPION_RUN
    if experiment.is_symlink() or (hasattr(experiment, "is_junction") and experiment.is_junction()):
        raise CleanupRefused(f"Champion path is a filesystem link: {experiment}")
    if not experiment.is_dir():
        raise CleanupRefused(f"CLEF champion experiment missing: {experiment}")
    try:
        resolved = resolve_checkpoint(experiment, "champion")
    except Exception as exc:
        raise CleanupRefused(f"CLEF best checkpoint cannot be verified: {exc}") from exc
    checkpoint = resolved.checkpoint
    if not checkpoint.is_relative_to((experiment / "checkpoints").resolve()):
        raise CleanupRefused(f"CLEF checkpoint resolves outside its experiment: {checkpoint}")
    if not checkpoint.is_dir():
        raise CleanupRefused(f"CLEF checkpoint is missing: {checkpoint}")
    return checkpoint


def create_plan(root: Path, cutoff: datetime) -> CleanupPlan:
    root = Path(root).expanduser().absolute()
    if root.name.lower() != "runs" or root.parent.name.lower() != "nanojev":
        raise CleanupRefused(f"Root must be a NanoJev/runs directory: {root}")
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise CleanupRefused(f"Root must not be a link/junction: {root}")
    if not root.is_dir():
        raise CleanupRefused(f"Run directory is missing: {root}")
    champion = validate_champion(root)
    cutoff_ns = int(cutoff.timestamp() * 1_000_000_000)
    retained = []
    candidates = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.casefold())
    except OSError as exc:
        raise CleanupRefused(f"Cannot enumerate {root}: {exc}") from exc
    for entry in entries:
        scanned = snapshot(entry)
        if entry.name == CHAMPION_RUN or scanned.unsafe or scanned.newest_ns >= cutoff_ns:
            retained.append(scanned)
        else:
            candidates.append(scanned)
    return CleanupPlan(root, cutoff, champion, tuple(retained), tuple(candidates))


def report(plan: CleanupPlan) -> dict:
    return {
        "schema": "nanojev-runs-cleanup-plan-v1",
        "root": str(plan.root),
        "cutoff_local_inclusive": plan.cutoff.isoformat(),
        "clef_champion_checkpoint": str(plan.champion),
        "retained_count": len(plan.retained),
        "candidate_count": len(plan.candidates),
        "potential_reclaim_bytes": plan.reclaim_bytes,
        "retained": [
            {"name": x.path.name, "bytes": x.bytes, "reason":
             "CLEF_CHAMPION" if x.path.name == CHAMPION_RUN else
             "UNSAFE_LINK_OR_SPECIAL_FILE" if x.unsafe else "RECENT_DESCENDANT"}
            for x in plan.retained
        ],
        "candidates": [
            {"name": x.path.name, "type": x.kind, "bytes": x.bytes,
             "files": x.files, "fingerprint": x.fingerprint}
            for x in plan.candidates
        ],
    }


def apply_plan(plan: CleanupPlan, *, confirmation: str) -> tuple[int, int]:
    if confirmation != CONFIRMATION:
        raise CleanupRefused(f"Deletion requires --confirm {CONFIRMATION}")
    # Validate the champion a second time immediately before any deletion.
    if validate_champion(plan.root) != plan.champion:
        raise CleanupRefused("CLEF champion changed since the plan was prepared")
    # Fail closed for *any* changed candidate; no partial deletion from stale plan.
    cutoff_ns = int(plan.cutoff.timestamp() * 1_000_000_000)
    for candidate in plan.candidates:
        if not candidate.path.exists() or candidate.path.is_symlink():
            raise CleanupRefused(f"Candidate disappeared or became a link: {candidate.path}")
        verified = snapshot(candidate.path)
        if (verified.unsafe or verified.newest_ns >= cutoff_ns
                or verified.fingerprint != candidate.fingerprint
                or verified.kind != candidate.kind):
            raise CleanupRefused(f"Run changed during cleanup preflight; no deletion: {candidate.path}")
    deleted_bytes = 0
    for candidate in plan.candidates:
        if candidate.kind == "directory":
            shutil.rmtree(candidate.path)
        elif candidate.kind == "file":
            candidate.path.unlink()
        else:
            raise CleanupRefused(f"Unsafe candidate type: {candidate.path}")
        deleted_bytes += candidate.bytes
        print(f"DELETED {candidate.path.name} ({candidate.bytes:,} bytes)")
    return len(plan.candidates), deleted_bytes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "NanoJev" / "runs")
    parser.add_argument("--since", default=DEFAULT_SINCE, metavar="YYYY-MM-DD",
                        help="Inclusive local calendar cutoff (default: 2026-10-22, fixed for this cleanup)")
    parser.add_argument("--report", type=Path, help="Write a JSON preview outside the runs directory")
    parser.add_argument("--apply", action="store_true", help="Delete candidates after verification")
    parser.add_argument("--confirm", default="", help=f"Required with --apply: {CONFIRMATION}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        try:
            cutoff = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError as exc:
            raise CleanupRefused("--since must be an ISO calendar date YYYY-MM-DD") from exc
        if args.apply and args.confirm != CONFIRMATION:
            raise CleanupRefused(f"Deletion requires --apply --confirm {CONFIRMATION}")
        plan = create_plan(args.root, cutoff)
        payload = report(plan)
        if args.report:
            output = args.report.expanduser().absolute()
            if output.is_relative_to(plan.root):
                raise CleanupRefused("JSON report cannot be placed inside the cleanup directory")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            print(f"Report: {output}")
        print(f"Root:      {plan.root}")
        print(f"Keep since: {cutoff.date()} (inclusive, local calendar day)")
        print(f"Champion:  {plan.champion}")
        print(f"Retained:  {len(plan.retained)} top-level entries")
        print(f"Candidates: {len(plan.candidates)} top-level entries")
        print(f"Reclaim:   {plan.reclaim_bytes / 1e9:.2f} GB (file-length estimate)")
        for candidate in sorted(plan.candidates, key=lambda s: s.bytes, reverse=True)[:20]:
            print(f"  REMOVE {candidate.bytes / 1e9:7.2f} GB {candidate.path.name}")
        if args.apply:
            count, bytes_freed = apply_plan(plan, confirmation=args.confirm)
            print(f"COMPLETE deleted={count} estimated_bytes={bytes_freed}")
        else:
            print(f"DRY RUN ONLY. To delete: python -m tools.nanojev_runs_cleanup --apply --confirm {CONFIRMATION}")
        return 0
    except (CleanupRefused, OSError) as exc:
        print(f"NANOJEV_CLEANUP_REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

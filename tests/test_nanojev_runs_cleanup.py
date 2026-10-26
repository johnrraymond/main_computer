"""Focused tests for conservative NanoJev runs cleanup."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import pytest

from tools import nanojev_runs_cleanup as cleanup

CUTOFF = datetime(2026, 10, 22)


def stamp(path: Path, date: datetime) -> None:
    ts = date.timestamp()
    os.utime(path, (ts, ts))


def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "NanoJev" / "runs"
    root.mkdir(parents=True)
    champ = root / cleanup.CHAMPION_RUN
    champ.mkdir()
    (champ / "training_state.json").write_text('{"best_checkpoint":"test"}', encoding="utf-8")
    checkpoint = champ / "checkpoints" / "cycle-1"
    checkpoint.mkdir(parents=True)
    monkeypatch.setattr(cleanup, "validate_champion", lambda observed: checkpoint)
    return root


def test_cutoff_keeps_entire_recent_run_and_old_champion(tmp_path, monkeypatch):
    root = tree(tmp_path, monkeypatch)
    old = root / "obsolete"
    old.mkdir()
    (old / "head.pt").write_bytes(b"old")
    stamp(old / "head.pt", datetime(2026, 10, 21, 23))
    stamp(old, datetime(2026, 10, 21, 23))
    recent = root / "recent"
    recent.mkdir()
    (recent / "older_parent.pt").write_bytes(b"0")
    stamp(recent / "older_parent.pt", datetime(2026, 10, 21))
    (recent / "updated.json").write_text("fresh", encoding="utf-8")
    stamp(recent / "updated.json", datetime(2026, 10, 22, 2))
    stamp(recent, datetime(2026, 10, 21))
    plan = cleanup.create_plan(root, CUTOFF)
    assert [p.path.name for p in plan.candidates] == ["obsolete"]
    assert {p.path.name for p in plan.retained} == {cleanup.CHAMPION_RUN, "recent"}
    assert plan.reclaim_bytes == 3
    assert old.is_dir()  # dry run makes no changes
    count, freed = cleanup.apply_plan(plan, confirmation=cleanup.CONFIRMATION)
    assert (count, freed) == (1, 3)
    assert not old.exists() and recent.exists() and (root / cleanup.CHAMPION_RUN).exists()


def test_root_files_follow_same_retention_policy(tmp_path, monkeypatch):
    root = tree(tmp_path, monkeypatch)
    old = root / "game_math_xy_v10_parent.pt"
    recent = root / "latest.json"
    old.write_bytes(b"old")
    recent.write_bytes(b"new")
    stamp(old, datetime(2026, 10, 17))
    stamp(recent, CUTOFF)
    plan = cleanup.create_plan(root, CUTOFF)
    assert [p.path.name for p in plan.candidates] == [old.name]
    assert recent in [p.path for p in plan.retained]


def test_changes_between_plan_and_apply_refuse_all_deletions(tmp_path, monkeypatch):
    root = tree(tmp_path, monkeypatch)
    for name in ("older1", "older2"):
        item = root / name
        item.mkdir()
        (item / "data").write_bytes(b"old")
        stamp(item / "data", datetime(2026, 10, 19))
        stamp(item, datetime(2026, 10, 19))
    plan = cleanup.create_plan(root, CUTOFF)
    (root / "older2" / "data").write_bytes(b"newer")
    with pytest.raises(cleanup.CleanupRefused, match="changed"):
        cleanup.apply_plan(plan, confirmation=cleanup.CONFIRMATION)
    assert (root / "older1").exists()
    assert (root / "older2").exists()


def test_bad_confirmation_or_missing_champion_refuses(tmp_path, monkeypatch):
    root = tree(tmp_path, monkeypatch)
    plan = cleanup.create_plan(root, CUTOFF)
    with pytest.raises(cleanup.CleanupRefused, match="--confirm"):
        cleanup.apply_plan(plan, confirmation="")
    monkeypatch.undo()
    # Without a real authoritative champion, the cleanup cannot plan deletion.
    with pytest.raises(cleanup.CleanupRefused):
        cleanup.create_plan(root, CUTOFF)


def test_linked_run_is_protected(tmp_path, monkeypatch):
    root = tree(tmp_path, monkeypatch)
    target = tmp_path / "external"
    target.mkdir()
    (target / "weights").write_bytes(b"sensitive")
    link = root / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not allowed on this system")
    plan = cleanup.create_plan(root, CUTOFF)
    assert link not in [s.path for s in plan.candidates]
    assert link in [s.path for s in plan.retained]


def test_nested_link_protects_entire_old_run(tmp_path, monkeypatch):
    root = tree(tmp_path, monkeypatch)
    target = tmp_path / "external"
    target.mkdir()
    old = root / "older"
    old.mkdir()
    try:
        (old / "link").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not allowed on this system")
    stamp(old, datetime(2026, 10, 18))
    plan = cleanup.create_plan(root, CUTOFF)
    assert old in [s.path for s in plan.retained]


def test_cli_preview_no_mutation(tmp_path, monkeypatch, capsys):
    root = tree(tmp_path, monkeypatch)
    old = root / "old"
    old.mkdir()
    stamp(old, datetime(2026, 10, 19))
    assert cleanup.main(["--root", str(root)]) == 0
    assert old.exists()
    assert "DRY RUN ONLY" in capsys.readouterr().out
    assert cleanup.main(["--root", str(root), "--apply"]) == 2
    assert old.exists()


def test_invalid_root_guard(tmp_path, monkeypatch):
    bad = tmp_path / "other" / "runs"
    bad.mkdir(parents=True)
    with pytest.raises(cleanup.CleanupRefused, match="NanoJev/runs"):
        cleanup.create_plan(bad, CUTOFF)

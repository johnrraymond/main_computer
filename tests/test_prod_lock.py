from __future__ import annotations

from pathlib import Path

from main_computer.prod_lock import (
    ProductionLockError,
    find_production_lock,
    require_unlocked_production_state,
)


def test_find_production_lock_returns_none_for_unlocked_tree(tmp_path: Path) -> None:
    assert find_production_lock(tmp_path, tmp_path / "runtime" / "deployments" / "dev" / "latest.json") is None


def test_require_unlocked_production_state_fails_closed_at_repo_root(tmp_path: Path) -> None:
    lock = tmp_path / ".prod.lock"
    lock.write_text('{"deployment":"prod","protected":true}\n', encoding="utf-8")

    try:
        require_unlocked_production_state(
            tmp_path,
            tmp_path / "runtime" / "deployments" / "dev" / "latest.json",
            action="rewrite deployment manifest",
        )
    except ProductionLockError as exc:
        message = str(exc)
        assert "rewrite deployment manifest" in message
        assert str(lock) in message
    else:
        raise AssertionError("expected production lock to block the write")


def test_require_unlocked_production_state_checks_external_target_ancestors(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    protected = tmp_path / "protected"
    target = protected / "runtime" / "deployments" / "dev" / "latest.json"
    target.parent.mkdir(parents=True)
    lock = protected / ".prod.lock"
    lock.write_text('{"deployment":"prod-local","protected":true}\n', encoding="utf-8")

    try:
        require_unlocked_production_state(repo, target, action="write outside repo")
    except ProductionLockError as exc:
        assert str(lock) in str(exc)
    else:
        raise AssertionError("expected production lock in target tree to block the write")

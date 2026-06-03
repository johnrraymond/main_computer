from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "antigit.py"


def _json_lines(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def test_snapshot_creates_named_external_directory_clone_and_ignores_gitignore_rules(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    (repo / ".gitignore").write_text("*.zip\n.env\ncache/\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=raw-local-machine-state\n", encoding="utf-8")
    (repo / "artifact.zip").write_bytes(b"not a real zip; still raw state")
    (repo / "cache").mkdir()
    (repo / "cache" / "runtime.txt").write_text("COUNT = 41\n", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("[core]\nrepositoryformatversion = 0\n", encoding="utf-8")
    source_listing_before = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*"))

    checkpoint_root = tmp_path / "checkpoint"
    created = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "snapshot",
            str(repo),
            "--checkpoint-root",
            str(checkpoint_root),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert created.returncode == 0, created.stdout + created.stderr
    payload = json.loads(created.stdout)
    assert payload["event"] == "antigit.snapshot.created"
    assert payload["checkpoint_name"] == "antigit_main_computer_test_checkpoint"
    assert payload["writes_to_source"] is False
    assert payload["uses_gitignore"] is False

    checkpoint = checkpoint_root / "antigit_main_computer_test_checkpoint"
    assert checkpoint.is_dir()
    assert not checkpoint.with_suffix(".zip").exists()

    assert (checkpoint / ".gitignore").read_text(encoding="utf-8") == "*.zip\n.env\ncache/\n"
    assert (checkpoint / ".env").read_text(encoding="utf-8") == "SECRET=raw-local-machine-state\n"
    assert (checkpoint / "artifact.zip").read_bytes() == b"not a real zip; still raw state"
    assert (checkpoint / "cache" / "runtime.txt").read_text(encoding="utf-8") == "COUNT = 41\n"
    assert (checkpoint / ".git" / "config").exists()

    source_listing_after = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*"))
    assert source_listing_after == source_listing_before


def test_signal_and_second_snapshot_track_changes_between_runs(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    target = repo / "counter.py"
    target.write_text("AMOUNT = 41\n\n\ndef value():\n    return AMOUNT\n", encoding="utf-8")
    checkpoint_root = tmp_path / "checkpoint"

    first = subprocess.run(
        [sys.executable, str(SCRIPT), "snapshot", str(repo), "--checkpoint-root", str(checkpoint_root), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stdout + first.stderr

    target.write_text("AMOUNT = 99\n\n\ndef value():\n    return AMOUNT\n", encoding="utf-8")
    (repo / "runtime.log").write_text("exit_code=7\n", encoding="utf-8")

    signal = subprocess.run(
        [sys.executable, str(SCRIPT), "signal", str(repo), "--checkpoint-root", str(checkpoint_root), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert signal.returncode == 0, signal.stdout + signal.stderr
    events = _json_lines(signal.stdout)
    file_events = [event for event in events if event["event"] == "antigit.signal"]
    summary = events[-1]
    assert summary["event"] == "antigit.signal.summary"
    assert summary["changed_files"] == 2

    counter_event = next(event for event in file_events if event["path"] == "counter.py")
    assert counter_event["action"] == "modified"
    assert "41" in counter_event["before_numeric_literals"]
    assert "99" in counter_event["after_numeric_literals"]
    assert "def" in counter_event["sopwith_stop_words"]

    log_event = next(event for event in file_events if event["path"] == "runtime.log")
    assert log_event["action"] == "added"
    assert "7" in log_event["numeric_literals"]

    second = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "snapshot",
            str(repo),
            "--checkpoint-root",
            str(checkpoint_root),
            "--emit-signal",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    second_events = _json_lines(second.stdout)
    assert any(event.get("path") == "counter.py" and event.get("action") == "modified" for event in second_events)
    assert second_events[-1]["event"] == "antigit.snapshot.created"

    checkpoint = checkpoint_root / "antigit_main_computer_test_checkpoint"
    assert (checkpoint / "counter.py").read_text(encoding="utf-8") == "AMOUNT = 99\n\n\ndef value():\n    return AMOUNT\n"
    assert (checkpoint / "runtime.log").read_text(encoding="utf-8") == "exit_code=7\n"

    clean = subprocess.run(
        [sys.executable, str(SCRIPT), "signal", str(repo), "--checkpoint-root", str(checkpoint_root), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    clean_events = _json_lines(clean.stdout)
    assert clean_events[-1]["changed_files"] == 0


def test_snapshot_refuses_to_write_checkpoint_inside_source_project(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    (repo / "counter.py").write_text("AMOUNT = 1\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "snapshot", str(repo), "--checkpoint-root", str(repo / "checkpoint")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must not be inside the source project" in result.stderr
    assert not (repo / "checkpoint").exists()



def test_snapshot_default_output_explains_what_antigit_is_doing(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    (repo / ".gitignore").write_text("*.zip\n", encoding="utf-8")
    (repo / "artifact.zip").write_bytes(b"raw zip-shaped machine state")
    (repo / "counter.py").write_text("AMOUNT = 12\n", encoding="utf-8")

    checkpoint_root = tmp_path / "checkpoint"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "snapshot", str(repo), "--checkpoint-root", str(checkpoint_root)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "antigit snapshot: starting external raw machine-state checkpoint." in result.stdout
    assert "source project:" in result.stdout
    assert "checkpoint directory:" in result.stdout
    assert "source project will not be edited" in result.stdout
    assert ".gitignore is copied as data but its ignore rules are not obeyed" in result.stdout
    assert "copying raw machine state" in result.stdout
    assert "antigit snapshot: complete." in result.stdout
    assert (checkpoint_root / "antigit_main_computer_test_checkpoint" / "artifact.zip").exists()

def test_antigit_guess_stop_words_expands_seed_words_deterministically() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "guess-stop-words", "sopwith", "pull", "numbers", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    words = payload["sopwith_stop_words"]
    assert words[:3] == ["sopwith", "stop", "word"]
    assert "restore" in words
    assert "integer" in words
    assert "float" in words

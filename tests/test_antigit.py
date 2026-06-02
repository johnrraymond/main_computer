from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "antigit.py"


def _json_lines(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def test_antigit_snapshot_signal_and_pull_restore_original_numbers(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "counter.py"
    target.write_text("AMOUNT = 41\n\n\ndef value():\n    return AMOUNT\n", encoding="utf-8")

    snapshot = tmp_path / "checkpoint.zip"
    created = subprocess.run(
        [sys.executable, str(SCRIPT), "snapshot", str(repo), "--output", str(snapshot), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    created_payload = json.loads(created.stdout)
    assert created_payload["event"] == "antigit.snapshot.created"
    assert created_payload["file_count"] == 1
    assert snapshot.exists()

    target.write_text("AMOUNT = 99\n\n\ndef value():\n    return AMOUNT\n", encoding="utf-8")

    signal = subprocess.run(
        [sys.executable, str(SCRIPT), "signal", str(snapshot), "--root", str(repo), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert signal.returncode == 0, signal.stdout + signal.stderr
    events = _json_lines(signal.stdout)
    assert [event["action"] for event in events] == ["restore"]
    assert events[0]["path"] == "counter.py"
    assert "41" in events[0]["numeric_literals"]
    assert "def" in events[0]["sopwith_stop_words"]

    dry_run = subprocess.run(
        [sys.executable, str(SCRIPT), "pull", str(snapshot), "--root", str(repo), "--dry-run", "--emit-signal", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
    assert "99" in target.read_text(encoding="utf-8")
    dry_events = _json_lines(dry_run.stdout)
    assert dry_events[-1]["event"] == "antigit.pull.summary"
    assert dry_events[-1]["dry_run"] is True
    assert dry_events[-1]["changed_files"] == 1

    pulled = subprocess.run(
        [sys.executable, str(SCRIPT), "pull", str(snapshot), "--root", str(repo), "--emit-signal", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert pulled.returncode == 0, pulled.stdout + pulled.stderr
    assert target.read_text(encoding="utf-8") == "AMOUNT = 41\n\n\ndef value():\n    return AMOUNT\n"


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

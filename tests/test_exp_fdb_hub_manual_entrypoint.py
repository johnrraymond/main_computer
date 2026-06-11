from __future__ import annotations

from pathlib import Path


def test_exp_fdb_hub_entrypoint_is_manual_and_declares_fdb_options() -> None:
    repo = Path(__file__).resolve().parents[1]
    entrypoint = (repo / "exp-fdb-hub.py").read_text(encoding="utf-8")
    module = (repo / "main_computer" / "exp_fdb_hub.py").read_text(encoding="utf-8")

    assert "main_computer.exp_fdb_hub" in entrypoint
    assert "Manual-only" in module
    assert "--cluster-file" in module
    assert "--namespace" in module
    assert "FoundationDB Docker cluster" in module


def test_standard_hub_module_does_not_import_experimental_fdb_hub() -> None:
    repo = Path(__file__).resolve().parents[1]
    hub_text = (repo / "main_computer" / "hub.py").read_text(encoding="utf-8")
    cli_text = (repo / "main_computer" / "cli.py").read_text(encoding="utf-8")

    assert "exp_fdb_hub" not in hub_text
    assert "exp_fdb_hub" not in cli_text

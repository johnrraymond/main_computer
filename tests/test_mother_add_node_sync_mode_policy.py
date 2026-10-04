from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "mother_mutate_harness.py"
spec = importlib.util.spec_from_file_location("mother_mutate_harness_sync_mode_under_test", HARNESS_PATH)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def _args(tmp_path: Path, *extra: str):
    return harness.build_parser().parse_args(
        [
            "add-node",
            "--runtime-state-root",
            str(tmp_path),
            "--network",
            "mainnet",
            "--node",
            "mainnetc-super3",
            "--host",
            "coolify-c",
            "--run-dir",
            str(tmp_path / "runs"),
            *extra,
        ]
    )


def _runner(
    tmp_path: Path,
    *,
    nodes: list[str],
    validators: list[str] | None = None,
    validator_count: int | None = None,
    extra: tuple[str, ...] = (),
) -> harness.Harness:
    tmp_path.mkdir(parents=True, exist_ok=True)
    baseline = tmp_path / "baseline.json"
    topology: dict[str, object] = {"nodes": nodes}
    if validators is not None:
        topology["validator_set"] = validators
    if validator_count is not None:
        topology["validator_count"] = validator_count
    baseline.write_text(
        json.dumps({"network": "mainnet", "final_topology": topology}),
        encoding="utf-8",
    )
    args = _args(tmp_path, "--baseline-evidence", str(baseline), *extra)
    args.baseline_evidence_sha256 = harness.canonical_sha256_file(baseline)
    return harness.Harness(args)


def test_add_node_sync_mode_defaults_full_below_two_established_validators(tmp_path: Path) -> None:
    assert _runner(tmp_path / "zero", nodes=[], validators=[]).resolved_add_node_sync_mode() == "FULL"
    assert _runner(
        tmp_path / "one",
        nodes=["mainneta-super1"],
        validators=["0xaaa"],
    ).resolved_add_node_sync_mode() == "FULL"


def test_failed_candidate_node_does_not_count_as_established_validator(tmp_path: Path) -> None:
    assert _runner(
        tmp_path,
        nodes=["mainneta-super1", "mainnetc-super3"],
        validators=["0xaaa"],
    ).resolved_add_node_sync_mode() == "FULL"


def test_add_node_sync_mode_defaults_snap_at_two_established_validators(tmp_path: Path) -> None:
    assert _runner(
        tmp_path,
        nodes=["mainneta-super1", "mainnetc-super3"],
        validators=["0xaaa", "0xbbb"],
    ).resolved_add_node_sync_mode() == "SNAP"


def test_add_node_sync_mode_uses_validator_count_when_set_is_absent(tmp_path: Path) -> None:
    assert _runner(
        tmp_path / "one",
        nodes=["mainneta-super1", "mainnetc-super3"],
        validator_count=1,
    ).resolved_add_node_sync_mode() == "FULL"
    assert _runner(
        tmp_path / "two",
        nodes=["mainneta-super1", "mainnetc-super3"],
        validator_count=2,
    ).resolved_add_node_sync_mode() == "SNAP"


def test_add_node_sync_mode_refuses_node_count_only_baseline(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        nodes=["mainneta-super1", "mainnetc-super3"],
    )
    try:
        runner.resolved_add_node_sync_mode()
    except SystemExit as exc:
        assert "established validator count" in str(exc)
    else:
        raise AssertionError("expected validator-count-only default guard")


def test_add_node_sync_mode_explicit_override_always_wins(tmp_path: Path) -> None:
    assert _runner(
        tmp_path / "snap",
        nodes=[],
        validators=[],
        extra=("--sync-mode", "SNAP"),
    ).resolved_add_node_sync_mode() == "SNAP"
    assert _runner(
        tmp_path / "full",
        nodes=["mainneta-super1", "mainnetc-super3", "mainnetb-super2"],
        validators=["0xaaa", "0xbbb", "0xccc"],
        extra=("--sync-mode", "FULL"),
    ).resolved_add_node_sync_mode() == "FULL"


def test_replica_sync_release_command_forwards_resolved_default(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        nodes=["mainneta-super1", "mainnetc-super3"],
        validators=["0xaaa"],
    )
    runner.state["identity_evidence"] = str(tmp_path / "identity.json")
    runner.state["identity_evidence_sha256"] = "a" * 64
    captured: list[str] = []

    def fake_run(_label: str, command: list[str]):
        captured.extend(command)
        return {"release_artifact": {"path": str(tmp_path / "release.json"), "sha256": "b" * 64}}

    runner.run = fake_run  # type: ignore[method-assign]
    runner.step_release_replica_sync()
    index = captured.index("--sync-mode")
    assert captured[index + 1] == "FULL"

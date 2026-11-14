from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "nanojev_clef_publish.py"


def load_tool():
    name = "nanojev_clef_publish_test_module"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_experiment(tmp_path: Path, *, complete: bool = True, checkpoint_name: str = "cycle-000131-reuse-004"):
    tool = load_tool()
    experiment_dir = tmp_path / "NanoJev" / "runs" / "structured"
    checkpoint = experiment_dir / "checkpoints" / checkpoint_name
    checkpoint.mkdir(parents=True)
    (checkpoint / "head.safetensors").write_bytes(b"head-weights-v1")
    (checkpoint / "tinystories.safetensors").write_bytes(b"tiny-weights-v1")
    (checkpoint / "optimizer.pt").write_bytes(b"do-not-publish-optimizer")
    (checkpoint / "rng_state.pt").write_bytes(b"do-not-publish-rng")
    meta = {
        "schema_version": tool.TRAINER_SCHEMA,
        "cycle": 131,
        "reuse_epoch": 4,
        "cycle_complete": complete,
        "global_step": 70960,
        "metrics": {
            "champion_selection_policy": "loss-first-accuracy-second-exact-tie-candidate",
            "winning_reuse_depth": 4,
            "progressive_champion_gating": True,
            "use_loss": True,
            "selection": {
                "overall": {"accuracy": 0.912109375, "mean_loss": 0.2739481056425685}
            },
        },
    }
    write_json(checkpoint / "meta.json", meta)
    write_json(
        experiment_dir / "experiment.json",
        {
            "schema_version": tool.TRAINER_SCHEMA,
            "source": {
                "model": "Qwen/Qwen3-0.6B",
                "revision": "c1899de289a04d12100db370d81485cdf75e47ca",
                "legacy_experiment": "C:/private/path/not-exported",
            },
            "contract": {
                "frozen_backbones": [
                    "Qwen/Qwen3-0.6B",
                    "EleutherAI/pythia-70m",
                    "roneneldan/TinyStories-33M",
                ],
                "structured_supervision_schema": "clef-routing-and-field-deep-supervision-v1",
                "structured_supervision_training_only": True,
                "structured_supervision_affects_inference": False,
                "task_composition": "task-composition-v1",
                "evidence_contract": "evidence-contract-v1",
                "training_task_mix_schema": "english-code-5pct-hard-task-rebalance-v1",
            },
        },
    )
    write_json(
        experiment_dir / "training_state.json",
        {
            "schema_version": tool.TRAINER_SCHEMA,
            "cycle": 131,
            "global_step": 70960,
            "latest_checkpoint": str(checkpoint),
            "best_checkpoint": str(checkpoint),
        },
    )
    return tool, experiment_dir, checkpoint


def test_default_champion_exports_immutable_checkpoint_release_without_training_state(tmp_path: Path):
    tool, experiment_dir, checkpoint = make_experiment(tmp_path)

    resolved = tool.resolve_checkpoint(experiment_dir)
    release = tool.export_release(resolved, tmp_path / "releases")

    assert resolved.checkpoint == checkpoint.resolve()
    assert release.release_name == "clef-cycle-000131-reuse-004"
    assert release.release_dir.name == release.release_name
    assert {path.name for path in release.release_dir.iterdir()} == {
        "head.safetensors",
        "tinystories.safetensors",
        "release.json",
        "SHA256_MANIFEST.json",
    }
    assert not (release.release_dir / "optimizer.pt").exists()
    assert not (release.release_dir / "rng_state.pt").exists()

    metadata = json.loads((release.release_dir / "release.json").read_text(encoding="utf-8"))
    assert metadata["schema_version"] == tool.RELEASE_SCHEMA
    assert metadata["checkpoint"]["name"] == checkpoint.name
    assert metadata["checkpoint"]["cycle_complete"] is True
    assert metadata["checkpoint"]["selection"]["accuracy"] == pytest.approx(0.912109375)
    assert metadata["backbone_provenance"]["source"] == {
        "model": "Qwen/Qwen3-0.6B",
        "revision": "c1899de289a04d12100db370d81485cdf75e47ca",
    }
    assert "legacy_experiment" not in json.dumps(metadata)

    manifest = tool.verify_local_release(release.release_dir, release.release_name)
    assert manifest == release.manifest
    assert manifest["files"]["head.safetensors"]["sha256"] == tool.sha256_file(
        checkpoint / "head.safetensors"
    )


def test_champion_export_refuses_unfinished_checkpoint(tmp_path: Path):
    tool, experiment_dir, _checkpoint = make_experiment(tmp_path, complete=False)

    with pytest.raises(tool.PublishError, match="unfinished checkpoint"):
        tool.resolve_checkpoint(experiment_dir)


def test_existing_release_is_idempotently_reused_and_never_overwritten(tmp_path: Path):
    tool, experiment_dir, checkpoint = make_experiment(tmp_path)
    resolved = tool.resolve_checkpoint(experiment_dir)
    first = tool.export_release(resolved, tmp_path / "releases")
    second = tool.export_release(resolved, tmp_path / "releases")
    assert first.reused_existing is False
    assert second.reused_existing is True

    (checkpoint / "head.safetensors").write_bytes(b"different-head")
    resolved_changed = tool.resolve_checkpoint(experiment_dir)
    with pytest.raises(tool.PublishError, match="refusing to overwrite immutable release"):
        tool.export_release(resolved_changed, tmp_path / "releases")


def test_explicit_checkpoint_name_can_export_historical_finalized_checkpoint(tmp_path: Path):
    tool, experiment_dir, _champion = make_experiment(tmp_path)
    historical = experiment_dir / "checkpoints" / "cycle-000127-reuse-002"
    historical.mkdir()
    (historical / "head.safetensors").write_bytes(b"old-head")
    (historical / "tinystories.safetensors").write_bytes(b"old-tiny")
    write_json(
        historical / "meta.json",
        {
            "schema_version": tool.TRAINER_SCHEMA,
            "cycle": 127,
            "reuse_epoch": 2,
            "cycle_complete": True,
            "global_step": 70000,
            "metrics": {},
        },
    )

    resolved = tool.resolve_checkpoint(experiment_dir, historical.name)
    assert resolved.checkpoint == historical.resolve()
    assert resolved.release_name == "clef-cycle-000127-reuse-002"


@dataclass
class FakeRef:
    name: str
    target_commit: str


class FakeApi:
    def __init__(self, *, tags=None, branches=None, fail_new_champion=False):
        self.tags = list(tags or [])
        self.branches = list(branches or [])
        self.actions = []
        self.fail_new_champion = fail_new_champion
        self._next_oid = "a" * 40

    def whoami(self):
        self.actions.append(("whoami",))
        return {"name": "alice"}

    def create_repo(self, **kwargs):
        self.actions.append(("create_repo", kwargs))
        return "https://huggingface.co/" + kwargs["repo_id"]

    def list_repo_refs(self, **kwargs):
        self.actions.append(("list_repo_refs", kwargs))
        return SimpleNamespace(branches=list(self.branches), tags=list(self.tags))

    def upload_folder(self, **kwargs):
        self.actions.append(("upload_folder", kwargs))
        return SimpleNamespace(oid=self._next_oid)

    def create_tag(self, **kwargs):
        self.actions.append(("create_tag", kwargs))
        self.tags.append(FakeRef(kwargs["tag"], kwargs["revision"]))

    def create_branch(self, **kwargs):
        self.actions.append(("create_branch", kwargs))
        if (
            self.fail_new_champion
            and kwargs["branch"] == "champion"
            and kwargs["revision"] == self._next_oid
        ):
            self.fail_new_champion = False
            raise RuntimeError("synthetic branch creation failure")
        self.branches = [b for b in self.branches if b.name != kwargs["branch"]]
        self.branches.append(FakeRef(kwargs["branch"], kwargs["revision"]))

    def delete_branch(self, **kwargs):
        self.actions.append(("delete_branch", kwargs))
        self.branches = [b for b in self.branches if b.name != kwargs["branch"]]


def remote_manifest_downloader(manifest: dict, tmp_path: Path):
    remote = tmp_path / "remote-SHA256_MANIFEST.json"
    write_json(remote, manifest)

    def download(**_kwargs):
        return str(remote)

    return download


def test_publish_defaults_repo_to_whoami_creates_immutable_tag_but_not_champion_alias(tmp_path: Path):
    tool, experiment_dir, _checkpoint = make_experiment(tmp_path)
    local = tool.export_release(tool.resolve_checkpoint(experiment_dir), tmp_path / "releases")
    api = FakeApi()

    result = tool.publish_release(
        local,
        api=api,
        downloader=remote_manifest_downloader(local.manifest, tmp_path),
    )

    assert result["repo_id"] == "alice/NanoJev-CLEF"
    assert result["immutable_tag"] == local.release_name
    assert result["champion_alias"] == "not-requested"
    assert any(action[0] == "upload_folder" for action in api.actions)
    tag = next(action for action in api.actions if action[0] == "create_tag")
    assert tag[1]["tag"] == local.release_name
    assert not any(
        action[0] == "create_branch" and action[1]["branch"] == "champion"
        for action in api.actions
    )


def test_set_as_champion_moves_alias_only_after_verified_immutable_publication(tmp_path: Path):
    tool, experiment_dir, _checkpoint = make_experiment(tmp_path)
    local = tool.export_release(tool.resolve_checkpoint(experiment_dir), tmp_path / "releases")
    old = "b" * 40
    api = FakeApi(branches=[FakeRef("champion", old)])

    result = tool.publish_release(
        local,
        repo_id="alice/NanoJev-CLEF",
        set_as_champion=True,
        api=api,
        downloader=remote_manifest_downloader(local.manifest, tmp_path),
    )

    assert result["champion_alias"] == "moved"
    assert next(b for b in api.branches if b.name == "champion").target_commit == "a" * 40
    names = [action[0] for action in api.actions]
    assert names.index("create_tag") < names.index("delete_branch") < names.index("create_branch")


def test_existing_identical_tag_is_idempotent_and_can_be_set_as_champion_without_upload(tmp_path: Path):
    tool, experiment_dir, _checkpoint = make_experiment(tmp_path)
    local = tool.export_release(tool.resolve_checkpoint(experiment_dir), tmp_path / "releases")
    existing_oid = "c" * 40
    api = FakeApi(tags=[FakeRef(local.release_name, existing_oid)])

    result = tool.publish_release(
        local,
        repo_id="alice/NanoJev-CLEF",
        set_as_champion=True,
        api=api,
        downloader=remote_manifest_downloader(local.manifest, tmp_path),
    )

    assert result["uploaded"] is False
    assert result["commit_oid"] == existing_oid
    assert result["champion_alias"] == "created"
    assert not any(action[0] == "upload_folder" for action in api.actions)
    assert not any(action[0] == "create_tag" for action in api.actions)


def test_failed_champion_move_restores_previous_alias(tmp_path: Path):
    tool, experiment_dir, _checkpoint = make_experiment(tmp_path)
    local = tool.export_release(tool.resolve_checkpoint(experiment_dir), tmp_path / "releases")
    old = "d" * 40
    api = FakeApi(branches=[FakeRef("champion", old)], fail_new_champion=True)

    with pytest.raises(tool.PublishError, match="restored old target"):
        tool.publish_release(
            local,
            repo_id="alice/NanoJev-CLEF",
            set_as_champion=True,
            api=api,
            downloader=remote_manifest_downloader(local.manifest, tmp_path),
        )

    assert next(b for b in api.branches if b.name == "champion").target_commit == old


def test_cli_contract_defaults_to_champion_and_exposes_set_as_champion_flag_only():
    tool = load_tool()
    args = tool.build_parser().parse_args(["--set-as-champion"])
    assert args.checkpoint == "champion"
    assert args.set_as_champion is True
    with pytest.raises(SystemExit):
        tool.build_parser().parse_args(["--token", "hf_should_never_be_on_cli"])

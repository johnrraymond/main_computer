#!/usr/bin/env python3
"""Export and publish a finalized NanoJev CLEF checkpoint to Hugging Face.

Normal use intentionally needs no checkpoint path::

    python .\\tools\\nanojev_clef_publish.py

That resolves the structured-supervision trainer's committed ``best_checkpoint``,
exports it under an immutable checkpoint-derived release name, publishes that
release, verifies the remote manifest, and creates an immutable Hub tag.

To additionally make the release the moving public CLEF champion::

    python .\\tools\\nanojev_clef_publish.py --set-as-champion

The ``champion`` alias is never implicit.  Hugging Face authentication is also
never accepted on the command line; ``huggingface_hub`` resolves its normal
ambient credential (for example ``HF_TOKEN`` or ``hf auth login`` state).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Callable, Iterable


TRAINER_SCHEMA = "main-computer-three-backbone-clef-tinystories-structured-supervision-train-v1"
RELEASE_SCHEMA = "main-computer-nanojev-clef-release-v1"
MANIFEST_SCHEMA = "main-computer-nanojev-clef-sha256-manifest-v1"
DEFAULT_EXPERIMENT_DIR = Path(
    os.environ.get(
        "NANOJEV_CLEF_EXPERIMENT_DIR",
        r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_structured_supervision_train_v1",
    )
)
DEFAULT_HF_REPO_NAME = "NanoJev-CLEF"
HF_REPO_ENV = "NANOJEV_CLEF_HF_REPO"
HF_RELEASE_FILES = (
    "head.safetensors",
    "tinystories.safetensors",
    "release.json",
    "SHA256_MANIFEST.json",
)


class PublishError(RuntimeError):
    """Fail-closed publication error with operator-readable context."""


@dataclass(frozen=True)
class ResolvedCheckpoint:
    experiment_dir: Path
    checkpoint: Path
    state: dict[str, Any]
    experiment: dict[str, Any]
    meta: dict[str, Any]
    selector: str
    release_name: str


@dataclass(frozen=True)
class LocalRelease:
    release_dir: Path
    release_name: str
    checkpoint: ResolvedCheckpoint
    manifest: dict[str, Any]
    reused_existing: bool


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublishError(f"required JSON file is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError(f"could not read JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PublishError(f"expected JSON object in {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    Path(path).write_text(encoded, encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    path = Path(path)
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _basename_any(path_text: str) -> str:
    normalized = str(path_text).replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1]


def _resolve_state_path(experiment_dir: Path, path_text: str) -> Path:
    """Resolve an authoritative state path, tolerating a relocated run directory."""
    candidate = Path(str(path_text)).expanduser()
    if candidate.exists():
        return candidate.resolve(strict=True)
    fallback = experiment_dir / "checkpoints" / _basename_any(str(path_text))
    if fallback.exists():
        return fallback.resolve(strict=True)
    raise PublishError(
        "training_state.json points to a checkpoint that cannot be resolved: "
        f"{path_text!r}; fallback={fallback}"
    )


def _resolve_explicit_checkpoint(experiment_dir: Path, selector: str) -> Path:
    supplied = Path(selector).expanduser()
    if supplied.exists():
        return supplied.resolve(strict=True)
    candidate = experiment_dir / "checkpoints" / selector
    if candidate.exists():
        return candidate.resolve(strict=True)
    raise PublishError(
        f"checkpoint selector {selector!r} is neither an existing path nor a checkpoint name in "
        f"{experiment_dir / 'checkpoints'}"
    )


def _release_name(checkpoint: Path) -> str:
    name = checkpoint.name
    if not name.startswith("cycle-"):
        raise PublishError(
            "publishable CLEF checkpoints must have a stable cycle-derived directory name; "
            f"observed {name!r}"
        )
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._")
    if not name or any(char not in allowed for char in name):
        raise PublishError(f"checkpoint name is not safe for a Hub revision: {name!r}")
    return f"clef-{name}"


def resolve_checkpoint(
    experiment_dir: Path,
    selector: str = "champion",
) -> ResolvedCheckpoint:
    experiment_dir = Path(experiment_dir).expanduser().resolve(strict=True)
    state = _read_json(experiment_dir / "training_state.json")
    experiment = _read_json(experiment_dir / "experiment.json")

    state_schema = state.get("schema_version")
    experiment_schema = experiment.get("schema_version")
    if state_schema != TRAINER_SCHEMA or experiment_schema != TRAINER_SCHEMA:
        raise PublishError(
            "unsupported structured-supervision experiment schema: "
            f"state={state_schema!r} experiment={experiment_schema!r} expected={TRAINER_SCHEMA!r}"
        )

    if selector == "champion":
        best = state.get("best_checkpoint")
        if not best:
            raise PublishError("training_state.json has no committed best_checkpoint")
        checkpoint = _resolve_state_path(experiment_dir, str(best))
    else:
        checkpoint = _resolve_explicit_checkpoint(experiment_dir, selector)

    meta = _read_json(checkpoint / "meta.json")
    if meta.get("schema_version") != TRAINER_SCHEMA:
        raise PublishError(
            f"unsupported checkpoint schema at {checkpoint}: {meta.get('schema_version')!r}"
        )
    if meta.get("cycle_complete") is not True:
        raise PublishError(
            f"refusing to publish unfinished checkpoint {checkpoint}; cycle_complete is not true"
        )
    for filename in ("head.safetensors", "tinystories.safetensors"):
        if not (checkpoint / filename).is_file():
            raise PublishError(f"finalized checkpoint is missing {filename}: {checkpoint}")

    if selector == "champion":
        authoritative = _resolve_state_path(experiment_dir, str(state["best_checkpoint"]))
        if checkpoint != authoritative:
            raise PublishError(
                "champion resolution drifted from training_state.json best_checkpoint: "
                f"resolved={checkpoint} authoritative={authoritative}"
            )

    cycle = meta.get("cycle")
    global_step = meta.get("global_step")
    if not isinstance(cycle, int) or cycle < 0:
        raise PublishError(f"checkpoint has invalid cycle: {cycle!r}")
    if not isinstance(global_step, int) or global_step < 0:
        raise PublishError(f"checkpoint has invalid global_step: {global_step!r}")

    return ResolvedCheckpoint(
        experiment_dir=experiment_dir,
        checkpoint=checkpoint,
        state=state,
        experiment=experiment,
        meta=meta,
        selector=selector,
        release_name=_release_name(checkpoint),
    )


def default_release_root(experiment_dir: Path) -> Path:
    configured = os.environ.get("NANOJEV_CLEF_RELEASE_ROOT")
    if configured:
        return Path(configured).expanduser()
    experiment_dir = Path(experiment_dir)
    # Normal layout is NanoJev/runs/<experiment>. Keep releases beside runs.
    if experiment_dir.parent.name.lower() == "runs":
        return experiment_dir.parent.parent / "releases" / "nanojev-clef"
    return experiment_dir.parent / "releases" / "nanojev-clef"


def _selection_release_summary(meta: dict[str, Any]) -> dict[str, Any]:
    metrics = meta.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    selection = metrics.get("selection") or metrics.get("predev")
    overall = selection.get("overall") if isinstance(selection, dict) else None
    summary: dict[str, Any] = {}
    if isinstance(overall, dict):
        if isinstance(overall.get("accuracy"), (int, float)):
            summary["accuracy"] = float(overall["accuracy"])
        if isinstance(overall.get("mean_loss"), (int, float)):
            summary["mean_loss"] = float(overall["mean_loss"])
    for key in (
        "champion_selection_policy",
        "winning_reuse_depth",
        "progressive_champion_gating",
        "use_loss",
    ):
        if key in metrics:
            summary[key] = metrics[key]
    return summary


def _portable_source_summary(experiment: dict[str, Any]) -> dict[str, Any]:
    source = experiment.get("source")
    source_summary: dict[str, Any] = {}
    if isinstance(source, dict):
        for key in ("model", "revision"):
            if source.get(key) is not None:
                source_summary[key] = source[key]
    contract = experiment.get("contract")
    contract_summary: dict[str, Any] = {}
    if isinstance(contract, dict):
        for key in (
            "frozen_backbones",
            "tinystories_layer_tap_schema",
            "tinystories_tapped_layers",
            "tinystories_residual_layers",
            "tinystories_anchor_layer",
            "structured_supervision_schema",
            "structured_supervision_training_only",
            "structured_supervision_affects_inference",
            "task_composition",
            "evidence_contract",
            "consensus_primary_objective",
            "consensus_direct_four_way_role",
        ):
            if key in contract:
                contract_summary[key] = contract[key]
    return {
        "source": source_summary,
        "contract": contract_summary,
        "exact_backbone_revision_coverage": (
            "qwen-only" if source_summary.get("revision") else "not-recorded"
        ),
    }


def build_release_metadata(resolved: ResolvedCheckpoint) -> dict[str, Any]:
    head = resolved.checkpoint / "head.safetensors"
    tiny = resolved.checkpoint / "tinystories.safetensors"
    return {
        "schema_version": RELEASE_SCHEMA,
        "model_family": "nanojev-clef",
        "release_name": resolved.release_name,
        "checkpoint": {
            "name": resolved.checkpoint.name,
            "schema_version": resolved.meta["schema_version"],
            "cycle": int(resolved.meta["cycle"]),
            "reuse_epoch": resolved.meta.get("reuse_epoch"),
            "global_step": int(resolved.meta["global_step"]),
            "cycle_complete": True,
            "selection": _selection_release_summary(resolved.meta),
        },
        "experiment": {
            "schema_version": resolved.experiment.get("schema_version"),
            "training_task_mix_schema": (
                (resolved.experiment.get("contract") or {}).get("training_task_mix_schema")
                if isinstance(resolved.experiment.get("contract"), dict)
                else None
            ),
        },
        "backbone_provenance": _portable_source_summary(resolved.experiment),
        "weights": {
            "head": {"file": head.name, **_file_record(head)},
            "tinystories": {"file": tiny.name, **_file_record(tiny)},
        },
        "excluded_training_state": ["optimizer.pt", "rng_state.pt"],
    }


def build_manifest(release_dir: Path, release_name: str) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for filename in ("head.safetensors", "tinystories.safetensors", "release.json"):
        path = Path(release_dir) / filename
        if not path.is_file():
            raise PublishError(f"release is missing {filename}: {release_dir}")
        files[filename] = _file_record(path)
    return {
        "schema_version": MANIFEST_SCHEMA,
        "release_name": release_name,
        "files": files,
    }


def verify_local_release(release_dir: Path, expected_release_name: str | None = None) -> dict[str, Any]:
    release_dir = Path(release_dir).resolve(strict=True)
    release = _read_json(release_dir / "release.json")
    manifest = _read_json(release_dir / "SHA256_MANIFEST.json")
    if release.get("schema_version") != RELEASE_SCHEMA:
        raise PublishError(f"unsupported local release schema in {release_dir}")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise PublishError(f"unsupported local manifest schema in {release_dir}")
    release_name = release.get("release_name")
    if not isinstance(release_name, str) or not release_name:
        raise PublishError(f"release.json has no release_name: {release_dir}")
    if expected_release_name is not None and release_name != expected_release_name:
        raise PublishError(
            f"release name mismatch: expected={expected_release_name!r} observed={release_name!r}"
        )
    if manifest.get("release_name") != release_name:
        raise PublishError("release.json and SHA256_MANIFEST.json disagree on release_name")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise PublishError("SHA256_MANIFEST.json has no files map")
    for filename in ("head.safetensors", "tinystories.safetensors", "release.json"):
        record = files.get(filename)
        path = release_dir / filename
        if not isinstance(record, dict) or not path.is_file():
            raise PublishError(f"manifest entry/file missing for {filename}")
        observed = _file_record(path)
        if record != observed:
            raise PublishError(
                f"local release hash/size mismatch for {filename}: expected={record} observed={observed}"
            )
    return manifest


def export_release(resolved: ResolvedCheckpoint, release_root: Path | None = None) -> LocalRelease:
    release_root = (
        Path(release_root).expanduser()
        if release_root is not None
        else default_release_root(resolved.experiment_dir)
    )
    release_root.mkdir(parents=True, exist_ok=True)
    final = release_root / resolved.release_name
    metadata = build_release_metadata(resolved)

    if final.exists():
        manifest = verify_local_release(final, resolved.release_name)
        existing = _read_json(final / "release.json")
        if existing != metadata:
            raise PublishError(
                "existing release directory does not match the requested checkpoint; "
                f"refusing to overwrite immutable release {final}"
            )
        return LocalRelease(
            release_dir=final.resolve(strict=True),
            release_name=resolved.release_name,
            checkpoint=resolved,
            manifest=manifest,
            reused_existing=True,
        )

    temp = release_root / f".{resolved.release_name}.tmp-{os.getpid()}"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=False, exist_ok=False)
    try:
        shutil.copy2(resolved.checkpoint / "head.safetensors", temp / "head.safetensors")
        shutil.copy2(
            resolved.checkpoint / "tinystories.safetensors",
            temp / "tinystories.safetensors",
        )
        _write_json(temp / "release.json", metadata)
        manifest = build_manifest(temp, resolved.release_name)
        _write_json(temp / "SHA256_MANIFEST.json", manifest)
        verify_local_release(temp, resolved.release_name)
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

    manifest = verify_local_release(final, resolved.release_name)
    return LocalRelease(
        release_dir=final.resolve(strict=True),
        release_name=resolved.release_name,
        checkpoint=resolved,
        manifest=manifest,
        reused_existing=False,
    )


def _ref_name(ref: Any) -> str | None:
    if isinstance(ref, dict):
        value = ref.get("name")
    else:
        value = getattr(ref, "name", None)
    return str(value) if value is not None else None


def _ref_target(ref: Any) -> str | None:
    if isinstance(ref, dict):
        value = ref.get("target_commit")
    else:
        value = getattr(ref, "target_commit", None)
    return str(value) if value is not None else None


def _iter_refs(refs: Any, kind: str) -> Iterable[Any]:
    if isinstance(refs, dict):
        value = refs.get(kind, [])
    else:
        value = getattr(refs, kind, [])
    return value or []


def _find_ref(refs: Any, kind: str, name: str) -> Any | None:
    return next((ref for ref in _iter_refs(refs, kind) if _ref_name(ref) == name), None)


def resolve_repo_id(api: Any, explicit_repo_id: str | None = None) -> str:
    repo_id = explicit_repo_id or os.environ.get(HF_REPO_ENV)
    if repo_id:
        if "/" not in repo_id.strip("/"):
            raise PublishError(
                f"Hugging Face repo id must be namespace/name, observed {repo_id!r}"
            )
        return repo_id.strip("/")
    identity = api.whoami()
    if not isinstance(identity, dict) or not identity.get("name"):
        raise PublishError(
            f"could not derive Hugging Face namespace from whoami(); set {HF_REPO_ENV} or --repo-id"
        )
    return f"{identity['name']}/{DEFAULT_HF_REPO_NAME}"


def _verify_remote_manifest(
    *,
    downloader: Callable[..., str],
    repo_id: str,
    revision: str,
    expected: dict[str, Any],
) -> None:
    try:
        path = downloader(
            repo_id=repo_id,
            filename="SHA256_MANIFEST.json",
            revision=revision,
            repo_type="model",
        )
    except Exception as exc:
        raise PublishError(
            f"could not verify remote manifest for {repo_id}@{revision}: {exc}"
        ) from exc
    observed = _read_json(Path(path))
    if observed != expected:
        raise PublishError(
            f"remote manifest mismatch for {repo_id}@{revision}; refusing to tag/alias it"
        )


def _move_champion_branch(api: Any, *, repo_id: str, target_commit: str) -> str:
    refs = api.list_repo_refs(repo_id=repo_id, repo_type="model")
    current = _find_ref(refs, "branches", "champion")
    old_target = _ref_target(current) if current is not None else None
    if old_target == target_commit:
        return "unchanged"
    if current is None:
        api.create_branch(
            repo_id=repo_id,
            repo_type="model",
            branch="champion",
            revision=target_commit,
            exist_ok=False,
        )
        return "created"

    api.delete_branch(repo_id=repo_id, repo_type="model", branch="champion")
    try:
        api.create_branch(
            repo_id=repo_id,
            repo_type="model",
            branch="champion",
            revision=target_commit,
            exist_ok=False,
        )
    except Exception as exc:
        # Best-effort rollback: do not intentionally leave a previously-valid
        # public champion alias absent if the replacement branch creation fails.
        try:
            api.create_branch(
                repo_id=repo_id,
                repo_type="model",
                branch="champion",
                revision=old_target,
                exist_ok=False,
            )
        except Exception as rollback_exc:
            raise PublishError(
                "failed to move champion alias and failed to restore its old target; "
                f"new={target_commit} old={old_target} error={exc} rollback_error={rollback_exc}"
            ) from exc
        raise PublishError(
            f"failed to move champion alias to {target_commit}; restored old target {old_target}: {exc}"
        ) from exc
    return "moved"


def publish_release(
    local: LocalRelease,
    *,
    repo_id: str | None = None,
    set_as_champion: bool = False,
    api: Any | None = None,
    downloader: Callable[..., str] | None = None,
) -> dict[str, Any]:
    if api is None or downloader is None:
        try:
            from huggingface_hub import HfApi, hf_hub_download
        except ImportError as exc:
            raise PublishError(
                "huggingface_hub is required to publish; install it in this Python environment "
                "or run with an environment that already provides it"
            ) from exc
        if api is None:
            api = HfApi()
        if downloader is None:
            downloader = hf_hub_download

    assert api is not None
    assert downloader is not None
    resolved_repo = resolve_repo_id(api, repo_id)
    api.create_repo(
        repo_id=resolved_repo,
        repo_type="model",
        private=False,
        exist_ok=True,
    )

    refs = api.list_repo_refs(repo_id=resolved_repo, repo_type="model")
    existing_tag = _find_ref(refs, "tags", local.release_name)
    uploaded = False
    if existing_tag is not None:
        commit_oid = _ref_target(existing_tag)
        if not commit_oid:
            raise PublishError(
                f"existing immutable tag {local.release_name} has no target commit"
            )
        _verify_remote_manifest(
            downloader=downloader,
            repo_id=resolved_repo,
            revision=local.release_name,
            expected=local.manifest,
        )
    else:
        commit = api.upload_folder(
            repo_id=resolved_repo,
            repo_type="model",
            folder_path=str(local.release_dir),
            path_in_repo=".",
            commit_message=f"Publish {local.release_name}",
        )
        commit_oid = getattr(commit, "oid", None)
        if not commit_oid:
            raise PublishError("Hugging Face upload did not return a commit oid")
        commit_oid = str(commit_oid)
        _verify_remote_manifest(
            downloader=downloader,
            repo_id=resolved_repo,
            revision=commit_oid,
            expected=local.manifest,
        )
        api.create_tag(
            repo_id=resolved_repo,
            repo_type="model",
            tag=local.release_name,
            revision=commit_oid,
            tag_message=f"NanoJev CLEF {local.checkpoint.checkpoint.name}",
            exist_ok=False,
        )
        uploaded = True

    champion_status = "not-requested"
    if set_as_champion:
        champion_status = _move_champion_branch(
            api,
            repo_id=resolved_repo,
            target_commit=commit_oid,
        )

    return {
        "ok": True,
        "repo_id": resolved_repo,
        "release_name": local.release_name,
        "checkpoint": local.checkpoint.checkpoint.name,
        "local_release": str(local.release_dir),
        "local_release_reused": bool(local.reused_existing),
        "uploaded": uploaded,
        "commit_oid": commit_oid,
        "immutable_tag": local.release_name,
        "set_as_champion": bool(set_as_champion),
        "champion_alias": champion_status,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment-dir",
        default=str(DEFAULT_EXPERIMENT_DIR),
        help="structured-supervision run directory containing training_state.json",
    )
    parser.add_argument(
        "--checkpoint",
        default="champion",
        help="'champion' (default), a checkpoint directory name, or an explicit finalized checkpoint path",
    )
    parser.add_argument(
        "--release-root",
        default=None,
        help="local export root; defaults beside the NanoJev runs directory",
    )
    parser.add_argument(
        "--repo-id",
        default=None,
        help=(
            "Hugging Face namespace/repo. Defaults to NANOJEV_CLEF_HF_REPO, otherwise "
            "<hf-whoami>/NanoJev-CLEF"
        ),
    )
    parser.add_argument(
        "--set-as-champion",
        action="store_true",
        help="after immutable publication succeeds, move the Hub 'champion' branch to this release",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve and export locally, but do not contact Hugging Face",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        resolved = resolve_checkpoint(Path(args.experiment_dir), args.checkpoint)
        local = export_release(
            resolved,
            None if args.release_root is None else Path(args.release_root),
        )
        if args.dry_run:
            result = {
                "ok": True,
                "dry_run": True,
                "checkpoint": resolved.checkpoint.name,
                "release_name": local.release_name,
                "local_release": str(local.release_dir),
                "local_release_reused": bool(local.reused_existing),
                "set_as_champion_requested": bool(args.set_as_champion),
                "hub_mutated": False,
            }
        else:
            result = publish_release(
                local,
                repo_id=args.repo_id,
                set_as_champion=bool(args.set_as_champion),
            )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

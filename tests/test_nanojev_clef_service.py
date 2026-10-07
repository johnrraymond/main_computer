from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "docker" / "nanojev" / "clef_service.py"
MANAGER_PATH = ROOT / "tools" / "nanojev_lifecycle_service.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_boolean_request_becomes_two_clef_answer_candidates_without_transport_id_in_prompt() -> None:
    service = load_module("nanojev_clef_service_test_boolean", SERVICE_PATH)
    question, ids, typ = service.build_question(
        "maze state",
        "clear_north",
        {
            "type": "boolean",
            "instructions": "Moving north is clear.",
            "criteria": {"false": "North is blocked.", "true": "North is clear."},
        },
    )

    assert typ == "boolean"
    assert ids == ["false", "true"]
    assert [candidate.candidate_id for candidate in question.candidates] == ids
    prompts = [candidate.paths[0].prompt for candidate in question.candidates]
    answers = [candidate.paths[0].answer for candidate in question.candidates]
    assert len(set(prompts)) == 1
    assert "clear_north" not in prompts[0]
    assert "Moving north is clear." in prompts[0]
    assert "False criterion: North is blocked." in prompts[0]
    assert "True criterion: North is clear." in prompts[0]
    assert answers == [" North is blocked.", " North is clear."]


def test_choice_and_score_wire_answers_preserve_existing_api_shape() -> None:
    service = load_module("nanojev_clef_service_test_answers", SERVICE_PATH)

    choice = service.answer_from_probabilities(["a", "b"], "choice", [0.25, 0.75])
    assert choice == {
        "type": "choice",
        "probabilities": {"a": 0.25, "b": 0.75},
        "choice": "b",
        "value": "b",
    }

    boolean = service.answer_from_probabilities(["false", "true"], "boolean", [0.1, 0.9])
    assert boolean["p_true"] == pytest.approx(0.9)
    assert boolean["value"] is True

    score = service.answer_from_probabilities(["0", "1", "2"], "score", [0.1, 0.2, 0.7])
    assert score["level"] == 2
    assert score["score"] == pytest.approx(1.6)
    assert score["value"] == pytest.approx(1.6)


def test_request_validation_enforces_service_limits() -> None:
    service = load_module("nanojev_clef_service_test_validate", SERVICE_PATH)
    payload = {
        "states": [
            {
                "id": "s1",
                "state": "state",
                "questions": {
                    "q1": {"type": "boolean", "instructions": "It is safe."},
                    "q2": {
                        "type": "choice",
                        "instructions": "Pick one.",
                        "criteria": {"left": "Go left", "right": "Go right"},
                    },
                },
            }
        ]
    }
    assert service.validate_request(payload) == payload["states"]

    too_many = {
        "states": [
            {
                "id": "s1",
                "state": "state",
                "questions": {
                    "q": {
                        "type": "choice",
                        "instructions": "Pick.",
                        "criteria": {str(i): f"candidate {i}" for i in range(255)},
                    }
                },
            },
            {
                "id": "s2",
                "state": "state",
                "questions": {"q": {"type": "boolean", "instructions": "True?"}},
            },
        ]
    }
    with pytest.raises(ValueError, match="256 CLEF candidate paths"):
        service.validate_request(too_many)


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_release_verification_accepts_publisher_schema_and_rejects_tampering(tmp_path: Path) -> None:
    service = load_module("nanojev_clef_service_test_release", SERVICE_PATH)
    root = tmp_path / "release"
    root.mkdir()
    (root / "head.safetensors").write_bytes(b"head")
    (root / "tinystories.safetensors").write_bytes(b"tiny")
    _write_json(
        root / "release.json",
        {
            "schema_version": service.RELEASE_SCHEMA,
            "model_family": "nanojev-clef",
            "release_name": "clef-cycle-000136-reuse-002",
        },
    )
    files = {}
    for filename in ("head.safetensors", "tinystories.safetensors", "release.json"):
        path = root / filename
        files[filename] = {"bytes": path.stat().st_size, "sha256": service.sha256_file(path)}
    _write_json(
        root / "SHA256_MANIFEST.json",
        {
            "schema_version": service.MANIFEST_SCHEMA,
            "release_name": "clef-cycle-000136-reuse-002",
            "files": files,
        },
    )

    release = service.verify_release(root)
    assert release["release_name"] == "clef-cycle-000136-reuse-002"

    (root / "head.safetensors").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        service.verify_release(root)


def test_manager_health_requires_requested_clef_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = load_module("nanojev_manager_clef_identity_test", MANAGER_PATH)
    controller = manager.ComposeNanoJevController(
        root=tmp_path,
        compose_file=tmp_path / "compose.yml",
        project_name="test",
        backend_port=9766,
        start_timeout_seconds=1,
        checkpoint_selector="champion",
        hf_repo="johnrraymond/NanoJev-CLEF",
    )

    monkeypatch.setattr(
        manager,
        "_http_json",
        lambda *_args, **_kwargs: {
            "ready": True,
            "model_loaded_once": True,
            "provider_calls": 0,
            "model_family": "nanojev-clef",
            "checkpoint_selector": "champion",
            "checkpoint_repo": "johnrraymond/NanoJev-CLEF",
        },
    )
    assert controller.health() is True

    monkeypatch.setattr(
        manager,
        "_http_json",
        lambda *_args, **_kwargs: {
            "ready": True,
            "model_loaded_once": True,
            "provider_calls": 0,
        },
    )
    assert controller.health() is False



def _make_verified_release(service, root: Path, release_name: str = "clef-cycle-000136-reuse-002") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "head.safetensors").write_bytes(b"head")
    (root / "tinystories.safetensors").write_bytes(b"tiny")
    _write_json(
        root / "release.json",
        {
            "schema_version": service.RELEASE_SCHEMA,
            "model_family": "nanojev-clef",
            "release_name": release_name,
        },
    )
    files = {}
    for filename in ("head.safetensors", "tinystories.safetensors", "release.json"):
        path = root / filename
        files[filename] = {"bytes": path.stat().st_size, "sha256": service.sha256_file(path)}
    _write_json(
        root / "SHA256_MANIFEST.json",
        {
            "schema_version": service.MANIFEST_SCHEMA,
            "release_name": release_name,
            "files": files,
        },
    )
    return root


def test_champion_resolution_uses_five_second_hf_probe_and_falls_back_to_last_verified_disk_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = load_module("nanojev_clef_service_test_offline_fallback", SERVICE_PATH)
    home = tmp_path / "hf"
    snapshot = _make_verified_release(service, home / "hub" / "models--johnrraymond--NanoJev-CLEF" / "snapshots" / "abc123")
    monkeypatch.setattr(service, "hf_home", lambda: home)
    service._write_local_state(
        repo_id="johnrraymond/NanoJev-CLEF",
        selector="champion",
        resolved_revision="abc123",
        snapshot=snapshot,
        release=service.verify_release(snapshot),
    )

    observed: dict[str, object] = {}

    class FakeApi:
        def model_info(self, *, repo_id, revision, timeout):
            observed.update(repo_id=repo_id, revision=revision, timeout=timeout)
            raise TimeoutError("hub did not answer")

    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.HfApi = lambda: FakeApi()

    def should_not_download(**_kwargs):
        raise AssertionError("snapshot download must not run after the 5-second reachability probe fails")

    fake_hf.snapshot_download = should_not_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    resolved_snapshot, commit, release, source, error = service.resolve_public_release(
        "johnrraymond/NanoJev-CLEF", "champion"
    )
    assert observed == {
        "repo_id": "johnrraymond/NanoJev-CLEF",
        "revision": "champion",
        "timeout": 5.0,
    }
    assert resolved_snapshot == snapshot.resolve()
    assert commit == "abc123"
    assert release["release_name"] == "clef-cycle-000136-reuse-002"
    assert source == "local-disk-fallback"
    assert error.startswith("TimeoutError:")


def test_successful_hf_resolution_records_exact_verified_disk_fallback_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = load_module("nanojev_clef_service_test_online_pointer", SERVICE_PATH)
    home = tmp_path / "hf"
    snapshot = _make_verified_release(service, tmp_path / "downloaded")
    monkeypatch.setattr(service, "hf_home", lambda: home)
    observed: dict[str, object] = {}

    class FakeApi:
        def model_info(self, *, repo_id, revision, timeout):
            observed["probe_timeout"] = timeout
            return SimpleNamespace(sha="deadbeef")

    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.HfApi = lambda: FakeApi()

    def fake_snapshot_download(**kwargs):
        observed["download"] = kwargs
        return str(snapshot)

    fake_hf.snapshot_download = fake_snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    resolved_snapshot, commit, release, source, error = service.resolve_public_release(
        "johnrraymond/NanoJev-CLEF", "champion"
    )
    assert resolved_snapshot == snapshot
    assert commit == "deadbeef"
    assert source == "huggingface"
    assert error == ""
    assert observed["probe_timeout"] == 5.0
    assert observed["download"]["revision"] == "deadbeef"
    assert observed["download"]["etag_timeout"] == 5.0

    state = json.loads(service.local_state_path().read_text(encoding="utf-8"))
    assert state["repo_id"] == "johnrraymond/NanoJev-CLEF"
    assert state["selector"] == "champion"
    assert state["resolved_revision"] == "deadbeef"
    assert Path(state["snapshot"]) == snapshot.resolve()
    assert state["release_name"] == release["release_name"]


def test_missing_or_invalid_hf_revision_does_not_silently_fall_back_to_stale_disk_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = load_module("nanojev_clef_service_test_no_stale_on_404", SERVICE_PATH)
    home = tmp_path / "hf"
    snapshot = _make_verified_release(service, home / "hub" / "models--johnrraymond--NanoJev-CLEF" / "snapshots" / "abc123")
    monkeypatch.setattr(service, "hf_home", lambda: home)
    service._write_local_state(
        repo_id="johnrraymond/NanoJev-CLEF",
        selector="champion",
        resolved_revision="abc123",
        snapshot=snapshot,
        release=service.verify_release(snapshot),
    )

    class NotFound(RuntimeError):
        def __init__(self):
            super().__init__("revision not found")
            self.response = SimpleNamespace(status_code=404)

    class FakeApi:
        def model_info(self, **_kwargs):
            raise NotFound()

    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.HfApi = lambda: FakeApi()
    fake_hf.snapshot_download = lambda **_kwargs: str(snapshot)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    with pytest.raises(NotFound, match="revision not found"):
        service.resolve_public_release("johnrraymond/NanoJev-CLEF", "does-not-exist")


def test_disk_fallback_can_recover_manifest_valid_cached_snapshot_before_pointer_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = load_module("nanojev_clef_service_test_cache_recovery", SERVICE_PATH)
    home = tmp_path / "hf"
    snapshot = _make_verified_release(
        service,
        home / "hub" / "models--johnrraymond--NanoJev-CLEF" / "snapshots" / "cafebabe",
        "clef-cycle-000140-reuse-003",
    )
    monkeypatch.setattr(service, "hf_home", lambda: home)

    recovered_snapshot, commit, release = service.load_last_verified_release("johnrraymond/NanoJev-CLEF")
    assert recovered_snapshot == snapshot
    assert commit == "cafebabe"
    assert release["release_name"] == "clef-cycle-000140-reuse-003"
    assert service.local_state_path().is_file()


def test_offline_fallback_forces_backbone_loading_to_local_files_only() -> None:
    service_text = SERVICE_PATH.read_text(encoding="utf-8")
    assert 'local_files_only=(self.checkpoint_source == "local-disk-fallback")' in service_text
    assert '"checkpoint_source": self.checkpoint_source' in service_text
    assert '"checkpoint_resolution_error": self.checkpoint_resolution_error' in service_text


def test_generic_pairwise_batch_surface_has_no_game_ownership_terms() -> None:
    source = SERVICE_PATH.read_text(encoding="utf-8")
    assert '"/api/evaluate-batch"' in source
    assert '"nanojev.pairwise-batch.v1"' in source
    assert '"nanojev.pairwise-batch-result.v1"' in source
    assert "_extract_bundle_batch" in source
    assert "_batched_head_forward" in source
    assert "captain" not in source.lower()


def test_docker_image_copies_generic_objective_api_for_batch_runtime() -> None:
    dockerfile = (ROOT / "docker" / "nanojev" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY tools/nanojev_objective_api.py /opt/main-computer-tools/" in dockerfile

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import sys
import threading


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "qemu_python_execution_smoke.py"
SPEC = importlib.util.spec_from_file_location("qemu_python_execution_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def fake_origin() -> dict:
    return {
        "implementation": "CPython",
        "version": "3.12.9",
        "major_minor": "3.12",
        "cache_tag": "cpython-312",
        "executable": r"C:\Python312\python.exe",
        "executable_sha256": "a" * 64,
        "prefix": r"C:\Python312",
        "base_prefix": r"C:\Python312",
        "platform": "win32",
        "machine": "AMD64",
    }


def test_request_identity_includes_exact_origin_python_and_excludes_transport() -> None:
    request = MODULE.build_execution_request(
        source="print(42)\n",
        inputs={"x": 1},
        timeout_seconds=5.0,
        origin_python=fake_origin(),
    )
    assert request["origin_python"]["executable"] == r"C:\Python312\python.exe"
    assert request["origin_python"]["executable_sha256"] == "a" * 64
    assert request["python_requirement"] == {"implementation": "CPython", "major_minor": "3.12"}
    assert request["request_id"] == MODULE.canonical_sha256(MODULE.request_identity_payload(request))

    # Placement is deliberately absent from the computation identity.
    assert "backend" not in request
    assert "remote" not in request
    assert "qemu" not in request


def test_result_identity_ignores_witness_but_not_semantic_output() -> None:
    base = {
        "schema": MODULE.SCHEMA_RESULT,
        "ok": True,
        "timed_out": False,
        "request_id": "r" * 64,
        "source_sha256": "s" * 64,
        "exit_code": 0,
        "stdout": "42\n",
        "stderr": "",
        "guest_python": {"major_minor": "3.12"},
    }
    one = MODULE.attach_result_identity({**base, "evidence": {"backend": "direct"}})
    two = MODULE.attach_result_identity({**base, "evidence": {"backend": "docker"}})
    assert one["result_id"] == two["result_id"]

    changed = MODULE.attach_result_identity({**base, "stdout": "43\n"})
    assert changed["result_id"] != one["result_id"]


def test_guest_result_marker_round_trip() -> None:
    request = MODULE.build_execution_request(
        source="print(42)\n",
        inputs={},
        timeout_seconds=5.0,
        origin_python=fake_origin(),
    )
    payload = {
        "schema": MODULE.SCHEMA_RESULT,
        "ok": True,
        "timed_out": False,
        "request_id": request["request_id"],
        "source_sha256": request["source_sha256"],
        "exit_code": 0,
        "stdout": "42\n",
        "stderr": "",
        "guest_python": {"major_minor": "3.12"},
    }
    encoded = base64.b64encode(MODULE.canonical_json_bytes(payload)).decode("ascii")
    output = "kernel line\n" + MODULE.RESULT_MARKER + encoded + "\npoweroff\n"
    result = MODULE.parse_guest_result(output, request=request)
    assert result["ok"] is True
    assert result["stdout"] == "42\n"
    assert len(result["result_id"]) == 64


def test_qemu_guest_args_force_tcg_no_network_and_fw_cfg() -> None:
    args = MODULE.QemuExecutionBackend.qemu_guest_args(
        kernel="KERNEL",
        initramfs="INITRAMFS",
        request_path="REQUEST",
        memory_mb=512,
    )
    joined = " ".join(args)
    assert "-accel tcg" in joined
    assert "-nic none" in joined
    assert "-kernel KERNEL" in joined
    assert "-initrd INITRAMFS" in joined
    assert "name=opt/main-computer/request,file=REQUEST" in joined
    assert "rdinit=/sbin/mc-qemu-init" in joined


def test_localhost_server_uses_same_request_and_preserves_result_identity() -> None:
    backend = MODULE.FakeDeterministicBackend()
    request = MODULE.build_execution_request(
        source="print(42)\n",
        inputs={},
        timeout_seconds=5.0,
        origin_python=fake_origin(),
    )
    direct = backend.execute(request)
    remote = MODULE.run_localhost_roundtrip(
        backend=backend,
        request=request,
        token="secret",
        timeout_seconds=5.0,
    )
    assert direct["request_id"] == request["request_id"]
    assert remote["request_id"] == request["request_id"]
    assert direct["result_id"] == remote["result_id"]


def test_server_rejects_wrong_token() -> None:
    backend = MODULE.FakeDeterministicBackend()
    request = MODULE.build_execution_request(
        source="print(42)\n",
        inputs={},
        timeout_seconds=5.0,
        origin_python=fake_origin(),
    )
    server = MODULE.ExecutionHTTPServer(("127.0.0.1", 0), backend, "right")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        client = MODULE.RemoteExecutionClient(
            f"http://{host}:{port}", bearer_token="wrong", timeout_seconds=5.0
        )
        try:
            client.execute(request)
        except MODULE.SmokeError as exc:
            assert "HTTP 401" in str(exc)
        else:
            raise AssertionError("wrong bearer token unexpectedly succeeded")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)





def test_direct_qemu_witness_hashes_qemu_without_starting_metadata_probe(monkeypatch, tmp_path) -> None:
    qemu = tmp_path / "qemu-system-x86_64.exe"
    qemu.write_bytes(b"fake-qemu")

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    kernel = runtime_dir / "vmlinuz"
    initramfs = runtime_dir / "initramfs.cpio.gz"
    kernel.write_bytes(b"kernel")
    initramfs.write_bytes(b"initramfs")
    (runtime_dir / "runtime.json").write_text(
        json.dumps(
            {
                "python_major_minor": "3.12",
                "kernel_sha256": MODULE.sha256_file(kernel),
                "initramfs_sha256": MODULE.sha256_file(initramfs),
            }
        ),
        encoding="utf-8",
    )

    config = MODULE.ExecutorConfig(
        backend="direct",
        qemu=str(qemu),
        docker=None,
        docker_qemu_image="unused",
        runtime_dir=runtime_dir,
        memory_mb=512,
        qemu_timeout_seconds=5.0,
    )
    backend = MODULE.QemuExecutionBackend(config)

    def forbidden_run(*args, **kwargs):
        raise AssertionError("QEMU metadata witness must not launch a subprocess")

    monkeypatch.setattr(MODULE.subprocess, "run", forbidden_run)
    witness = backend._qemu_witness([str(qemu)])

    assert witness["qemu_executable"] == str(qemu)
    assert witness["qemu_executable_sha256"] == MODULE.sha256_file(qemu)
    assert "qemu_version" not in witness


def test_guest_init_uses_explicit_official_python_path() -> None:
    guest_init = (ROOT / "docker" / "qemu-python-smoke" / "guest_init.sh").read_text(encoding="utf-8")
    assert 'export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"' in guest_init
    assert 'PYTHON=/usr/local/bin/python3' in guest_init
    assert '"$PYTHON" /opt/main-computer/qemu_python_guest_runner.py' in guest_init


def test_guest_runtime_builder_records_revision_and_python_path() -> None:
    builder = (ROOT / "docker" / "qemu-python-smoke" / "build_guest_runtime.sh").read_text(encoding="utf-8")
    assert 'PYTHON=/usr/local/bin/python3' in builder
    assert '"runtime_revision": 2' in builder
    assert '"python_executable": "/usr/local/bin/python3"' in builder
    assert MODULE.GUEST_RUNTIME_REVISION == 2



def test_execution_series_continues_after_one_failure_and_reports_timing() -> None:
    request = MODULE.build_execution_request(
        source="print(42)\n",
        inputs={},
        timeout_seconds=5.0,
        origin_python=fake_origin(),
    )
    backend = MODULE.FakeDeterministicBackend()
    calls = {"count": 0}

    def flaky_execute(value: dict) -> dict:
        calls["count"] += 1
        if calls["count"] == 2:
            raise MODULE.SmokeError("QEMU execution exceeded 5.0s")
        return backend.execute(value)

    first, runs = MODULE.run_execution_series(
        execute=flaky_execute,
        request=request,
        count=3,
    )
    summary = MODULE.summarize_execution_series(
        runs,
        expected_request_id=request["request_id"],
    )

    assert first is not None
    assert calls["count"] == 3
    assert [run["ok"] for run in runs] == [True, False, True]
    assert runs[1]["timed_out"] is True
    assert summary["attempted"] == 3
    assert summary["completed"] == 2
    assert summary["failed"] == 1
    assert summary["all_completed"] is False
    assert summary["request_preserved"] is False
    assert summary["result_identity_consistent"] is False
    assert summary["wall_seconds"]["count"] == 3


def test_repeated_localhost_server_preserves_same_request_and_result_identity() -> None:
    backend = MODULE.FakeDeterministicBackend()
    request = MODULE.build_execution_request(
        source="print(42)\n",
        inputs={},
        timeout_seconds=5.0,
        origin_python=fake_origin(),
    )
    first, runs = MODULE.run_localhost_roundtrips(
        backend=backend,
        request=request,
        token="secret",
        timeout_seconds=5.0,
        count=4,
    )
    summary = MODULE.summarize_execution_series(
        runs,
        expected_request_id=request["request_id"],
    )

    assert first is not None
    assert len(runs) == 4
    assert summary["attempted"] == 4
    assert summary["completed"] == 4
    assert summary["failed"] == 0
    assert summary["all_completed"] is True
    assert summary["request_preserved"] is True
    assert summary["result_identity_consistent"] is True
    assert summary["result_ids"] == [first["result_id"]]


def test_repeat_must_be_positive() -> None:
    request = MODULE.build_execution_request(
        source="print(42)\n",
        inputs={},
        timeout_seconds=5.0,
        origin_python=fake_origin(),
    )
    try:
        MODULE.run_execution_series(
            execute=MODULE.FakeDeterministicBackend().execute,
            request=request,
            count=0,
        )
    except MODULE.SmokeError as exc:
        assert "--repeat must be at least 1" in str(exc)
    else:
        raise AssertionError("zero repeat count unexpectedly accepted")

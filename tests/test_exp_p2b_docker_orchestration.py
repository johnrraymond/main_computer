"""Simulated subprocess boundaries; no Docker, FDB, or chain writes."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer import exp_fdb_docker_supervision as sup


class FakeStarter:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code

    def wait(self, timeout=None):
        assert timeout is not None
        return self.exit_code

    def poll(self):
        return self.exit_code


def worker(tmp_path, monkeypatch, *, container_code="0", cleanup_code=0, timed_out=False):
    commands = []

    def fake_run(command, **kw):
        commands.append(command)
        assert kw["timeout"] > 0
        if "ps" in command:
            return subprocess.CompletedProcess(command, 0, "container-123\n", "")
        if "wait" in command:
            if timed_out:
                raise subprocess.TimeoutExpired(command, kw["timeout"])
            return subprocess.CompletedProcess(command, 0, container_code + "\n", "")
        if "logs" in command:
            return subprocess.CompletedProcess(command, 0, "worker-lab PASS\n", "")
        if "down" in command:
            return subprocess.CompletedProcess(command, cleanup_code, "", "cleanup failed" if cleanup_code else "")
        raise AssertionError(command)

    monkeypatch.setattr(sup.subprocess, "run", fake_run)
    w = sup.DetachedComposeWorker(FakeStarter(), compose_base=["docker", "compose", "-p", "mc-exp-abc"],
                                  container_base=["docker"], repo_root=tmp_path, environment={},
                                  startup_timeout=10, execution_timeout=25)
    return w, commands


def test_exited_container_finishes_even_if_compose_would_stay_attached(tmp_path, monkeypatch):
    w, commands = worker(tmp_path, monkeypatch)
    assert w.wait() == 0
    assert w.wait() == 0
    assert len([c for c in commands if "wait" in c]) == 1
    assert len([c for c in commands if "down" in c]) == 1
    assert w.container_id == "container-123"


def test_worker_exit_nonzero_is_failure(tmp_path, monkeypatch):
    w, _ = worker(tmp_path, monkeypatch, container_code="7")
    assert w.wait() == 7


def test_wait_timeout_is_bounded_and_compose_cleaned(tmp_path, monkeypatch):
    w, commands = worker(tmp_path, monkeypatch, timed_out=True)
    assert w.wait() == 1
    assert any("down" in c for c in commands)


def test_cleanup_failure_cannot_be_claimed_as_success(tmp_path, monkeypatch):
    w, _ = worker(tmp_path, monkeypatch, cleanup_code=1)
    assert w.wait() == 1


def test_journal_guard_prevents_any_same_id_replay(tmp_path):
    path = tmp_path / "run-integrated-stages.json"
    record = sup.RunJournal(path, run_id="run-x", live_chain=True)
    record.advance("p2b_executing", payout_outcome="unknown_requires_reconciliation")
    stored = json.loads(path.read_text())
    assert stored["payout_outcome"] == "unknown_requires_reconciliation"
    assert stored["phase"] == "p2b_executing"
    with pytest.raises(RuntimeError, match="already has an execution journal"):
        sup.RunJournal(path, run_id="run-x", live_chain=True)


def test_bounded_p2a_cli_handles_success_and_bad_evidence(tmp_path, monkeypatch):
    calls = []
    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"ok":true,"phase":"baseline-created"}', "")
    monkeypatch.setattr(sup.subprocess, "run", fake_run)
    payload = sup.verify_checkpoint_bounded(repo_root=tmp_path, cluster_file=tmp_path/"cluster",
        session_id="sess-1", checkpoint=tmp_path/"checkpoint", temporal_address="127.0.0.1:7233", timeout=12)
    assert payload["ok"] is True
    assert calls[0][1]["timeout"] == 12
    assert "--verify-exp-temporal" in calls[0][0]

    monkeypatch.setattr(sup.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 1, '{"ok":false,"error":"not ready"}', ""))
    with pytest.raises(RuntimeError, match="not ready"):
        sup.verify_checkpoint_bounded(repo_root=tmp_path, cluster_file=tmp_path/"cluster",
            session_id="sess-1", checkpoint=tmp_path/"checkpoint", temporal_address="127.0.0.1:7233", timeout=12)


def test_live_chain_verification_requires_report_and_its_ok_field(tmp_path, monkeypatch):
    kwargs = dict(repo_root=tmp_path, hub_urls=["http://127.0.0.1:8970", "http://127.0.0.1:8971"],
                  dev_chain_state=tmp_path / "latest.json", rpc_url="http://127.0.0.1:18545",
                  chain_id=42424242, timeout=90, report_path=tmp_path / "report.json")
    monkeypatch.setattr(sup.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, '{"ok":true}', ""))
    with pytest.raises(RuntimeError, match="without durable settlement report"):
        sup.verify_live_chain_bounded(**kwargs)
    kwargs["report_path"].write_text('{"ok":false,"error":"receipt mismatch"}')
    with pytest.raises(RuntimeError, match="receipt mismatch"):
        sup.verify_live_chain_bounded(**kwargs)
    kwargs["report_path"].write_text('{"ok":true,"dev_chain_tx_hash":"0xabc"}')
    assert sup.verify_live_chain_bounded(**kwargs)["ok"] is True


def test_live_launcher_aborts_before_chain_when_p2a_fails(tmp_path, monkeypatch):
    import main_computer.exp_fdb_hub as hub
    events = []
    class Server:
        server_port = 8970
        temporal_runtime = object()
        def serve_forever(self): pass
        def shutdown(self): pass
        def server_close(self): pass
    class Container:
        def wait(self, timeout=None):
            events.append("docker_done")
            return 0
        def poll(self): return 0
    def fake_launch(args, *, hub_base_urls):
        out = hub.output_dir_for_live_lab(args)
        run = hub._ensure_scheduler_lab_run_id(args)
        (out/f"{run}-live-summary.json").write_text(json.dumps({"ok":True,"run_id":run,"mode":"live_session_v1","invariant_violations":0,"requests_accepted":3}))
        (out/f"{run}-live-events.jsonl").write_text(json.dumps({"session_ids":["sess-1"]})+"\n")
        return Container()
    monkeypatch.setattr(hub, "ensure_foundationdb_smoke_loaded", lambda args: None)
    monkeypatch.setattr(hub, "create_exp_fdb_hub_server", lambda args, *, port: Server())
    monkeypatch.setattr(hub, "launch_scheduler_lab_docker", fake_launch)
    monkeypatch.setattr(sup, "verify_checkpoint_bounded", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("P2A failed")))
    monkeypatch.setattr(sup, "verify_live_chain_bounded", lambda **kwargs: pytest.fail("MUST NOT PAY"))
    args = hub.build_parser().parse_args(["--repo-root", str(tmp_path), "--ports","8970,8971,8972",
         "--docker", "--host","0.0.0.0", "--docker-request-mode","live_session_v1",
         "--require-multisession-auth", "--live-chain-smoke", "--live-chain-smoke-rpc","http://127.0.0.1:18545",
         "--live-chain-smoke-chain-id","42424242", "--docker-output-dir",str(tmp_path/"out")])
    assert hub.serve_exp_fdb_hubs(args) == 1
    run = args.scheduler_lab_run_id
    stage = json.loads((tmp_path/"out"/f"{run}-integrated-stages.json").read_text())
    assert stage["phase"] == "p2a_failed"
    assert stage["payout_outcome"] == "not_started"


def test_live_launcher_marks_unknown_before_chain_call_and_fails_closed(tmp_path, monkeypatch):
    import main_computer.exp_fdb_hub as hub
    class Server:
        server_port = 8970
        temporal_runtime = object()
        def serve_forever(self): pass
        def shutdown(self): pass
        def server_close(self): pass
    class Container:
        def wait(self, timeout=None): return 0
        def poll(self): return 0
    def fake_launch(args, *, hub_base_urls):
        out = hub.output_dir_for_live_lab(args)
        run = hub._ensure_scheduler_lab_run_id(args)
        (out/f"{run}-live-summary.json").write_text(json.dumps({"ok":True,"run_id":run,"mode":"live_session_v1","invariant_violations":0,"requests_accepted":3}))
        (out/f"{run}-live-events.jsonl").write_text(json.dumps({"session_ids":["sess-1"]})+"\n")
        return Container()
    monkeypatch.setattr(hub, "ensure_foundationdb_smoke_loaded", lambda args: None)
    monkeypatch.setattr(hub, "create_exp_fdb_hub_server", lambda args, *, port: Server())
    monkeypatch.setattr(hub, "launch_scheduler_lab_docker", fake_launch)
    monkeypatch.setattr(sup, "verify_checkpoint_bounded", lambda **kw: {"ok":True,"phase":"baseline-created"})
    def fake_chain(**kw):
        journals = list((tmp_path/"out").glob("*-integrated-stages.json"))
        assert len(journals) == 1
        stage = json.loads(journals[0].read_text())
        assert stage["payout_outcome"] == "unknown_requires_reconciliation"
        raise subprocess.TimeoutExpired(["chain"], 60)
    monkeypatch.setattr(sup, "verify_live_chain_bounded", fake_chain)
    args = hub.build_parser().parse_args(["--repo-root", str(tmp_path), "--ports","8970,8971,8972",
         "--docker", "--host","0.0.0.0", "--docker-request-mode","live_session_v1",
         "--require-multisession-auth", "--live-chain-smoke", "--live-chain-smoke-rpc","http://127.0.0.1:18545",
         "--live-chain-smoke-chain-id","42424242", "--docker-output-dir",str(tmp_path/"out")])
    assert hub.serve_exp_fdb_hubs(args) == 1
    run = args.scheduler_lab_run_id
    stage = json.loads((tmp_path/"out"/f"{run}-integrated-stages.json").read_text())
    assert stage["phase"] == "p2b_failed_requires_reconciliation"
    assert stage["payout_outcome"] == "unknown_requires_reconciliation"
    with pytest.raises(RuntimeError, match="already has an execution journal"):
        hub.serve_exp_fdb_hubs(args)


def test_chain_child_calls_experimental_e2e_not_stable_default(monkeypatch, tmp_path):
    from tools.exp_hub_lab import p2b_live_verifier
    from tools.exp_hub_lab import run_lab
    calls = []
    def fake_verify(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "dev_chain_tx_hash": "0xabc"}
    monkeypatch.setattr(run_lab, "verify_full_e2e", fake_verify)
    ret = p2b_live_verifier.main([
        "--hub-urls", "http://127.0.0.1:8970,http://127.0.0.1:8971",
        "--dev-chain-state", str(tmp_path/"latest.json"),
        "--rpc-url", "http://127.0.0.1:18545", "--chain-id", "42424242",
        "--report-path", str(tmp_path/"report.json"), "--json",
    ])
    assert ret == 0
    assert calls[0]["require_multisession_auth"] is True
    assert calls[0]["chain_id"] == 42424242


def test_chain_child_rejects_non_loopback_rpc(monkeypatch, tmp_path):
    from tools.exp_hub_lab import p2b_live_verifier
    from tools.exp_hub_lab import run_lab
    monkeypatch.setattr(run_lab, "verify_full_e2e", lambda **kwargs: pytest.fail("MUST NOT BROADCAST"))
    with pytest.raises(SystemExit, match="2"):
        p2b_live_verifier.main([
            "--hub-urls", "http://127.0.0.1:8970,http://127.0.0.1:8971",
            "--dev-chain-state", str(tmp_path/"latest.json"),
            "--rpc-url", "http://192.168.1.2:18545", "--chain-id", "42424242",
            "--report-path", str(tmp_path/"report.json"),
        ])


def test_chain_child_prints_structured_failure_reason(monkeypatch, tmp_path, capsys):
    from tools.exp_hub_lab import p2b_live_verifier
    from tools.exp_hub_lab import run_lab
    monkeypatch.setattr(run_lab, "verify_full_e2e", lambda **kwargs: {
        "ok": False, "error": "worker settlement identity mismatch", "dev_chain_tx_hash": None,
    })
    code = p2b_live_verifier.main([
        "--hub-urls", "http://127.0.0.1:8970,http://127.0.0.1:8972",
        "--dev-chain-state", str(tmp_path / "latest.json"),
        "--rpc-url", "http://127.0.0.1:18545", "--chain-id", "42424242",
        "--report-path", str(tmp_path / "report.json"), "--json",
    ])
    assert code == 1
    emitted = json.loads(capsys.readouterr().out.strip())
    assert emitted["error"] == "worker settlement identity mismatch"
    assert emitted["dev_chain_tx_hash"] is None

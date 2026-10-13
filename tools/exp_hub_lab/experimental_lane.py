"""Explicit local-only Experimental Hub lane for Stable/Experimental handoff comparison.

The normal exp_hub_lab command still runs Stable Hub.  This lane deliberately
runs the *real* exp_fdb_hub server on separate loopback ports, with mock-chain
accounting and a separate FoundationDB namespace. It never touches Mother.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from typing import Sequence


EXP_PORTS = (8970, 8971, 8972)
EXP_URLS = tuple(f"http://127.0.0.1:{port}" for port in EXP_PORTS)
EXP_NAMESPACE = "main-computer-exp-handoff-parity"


def _require_ports_free() -> None:
    for port in EXP_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Experimental Hub parity port {port} is already occupied. Refusing to start mixed Hub versions.")


def run_lane(argv: Sequence[str]) -> int:
    from tools.exp_hub_lab import run_lab as exp_lab

    parser = argparse.ArgumentParser(description="Local-only real Experimental Hub handoff comparison")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve-exp-cluster", action="store_true")
    mode.add_argument("--serve-exp-docker-lab", action="store_true", help="Integrated Hubs + existing Docker worker lab using P1 live-session protocol")
    mode.add_argument("--verify-exp-handoff", action="store_true")
    mode.add_argument("--verify-exp-accounting", action="store_true")
    mode.add_argument("--verify-exp-stress", action="store_true")
    mode.add_argument("--verify-exp-recovery", action="store_true")
    mode.add_argument("--verify-exp-temporal", action="store_true")
    mode.add_argument("--compare-handoff", action="store_true")
    parser.add_argument("--cluster-file", type=Path, default=Path(".foundationdb/docker.cluster"))
    parser.add_argument("--live-chain", action="store_true", help="Explicitly enable local dev-chain RPC, real bridge backend, and on-chain payout smoke")
    parser.add_argument("--live-chain-state", type=Path, default=Path("runtime/deployments/dev/latest.json"), help="Local dev-chain deployment state; must identify a dev network")
    parser.add_argument("--live-chain-rpc", default="", help="Explicit local host RPC override; defaults to deployment host_rpc_url")
    parser.add_argument("--temporal-address", default="", help="Experimental Temporal server address (or MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS)")
    parser.add_argument("--temporal-session-id", default="", help="Completed Experimental paid session ID to verify")
    parser.add_argument("--temporal-checkpoint", type=Path, default=Path("runtime/exp-hub-lab/p2a-temporal-checkpoint.json"),
                        help="First invocation writes immutable baseline; second compares it after restarting Hub and Temporal worker")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--docker-workers", type=int, default=10)
    parser.add_argument("--docker-requesters", type=int, default=2)
    parser.add_argument("--docker-live-concurrency", type=int, default=2)
    parser.add_argument("--docker-live-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--docker-run-timeout-seconds", type=float, default=0.0, help="Worker container overall timeout (0=derived)")
    parser.add_argument("--docker-output-dir", type=Path, default=Path("runtime/scheduler-lab/exp-fdb"))
    parser.add_argument("--verify-exp-stress-runs", type=int, default=10)
    parser.add_argument("--verify-exp-stress-parallel", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv))
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.live_chain and not (args.serve_exp_cluster or args.serve_exp_docker_lab):
        parser.error("--live-chain is only supported with --serve-exp-cluster or --serve-exp-docker-lab")

    if args.serve_exp_cluster or args.serve_exp_docker_lab:
        from main_computer.exp_fdb_hub import main as exp_main

        try:
            _require_ports_free()
        except RuntimeError as exc:
            print(f"Experimental Hub lane blocked: {exc}")
            return 2
        chain_config = None
        if args.live_chain:
            from tools.exp_hub_lab.live_chain import preflight_local_dev_chain
            try:
                chain_config = preflight_local_dev_chain(
                    args.live_chain_state, rpc_override=args.live_chain_rpc,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                print(f"Experimental live-chain preflight: FAIL: {exc}", flush=True)
                return 2
            print("Experimental live-chain preflight: PASS " + json.dumps(chain_config.public_status(), sort_keys=True), flush=True)
        if args.serve_exp_docker_lab:
            if not os.environ.get("MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS", "").strip():
                parser.error("Integrated P2-A Docker lab requires MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS; start persistent Temporal first")
            if args.docker_workers < 1 or args.docker_requesters < 1:
                parser.error("Docker live lab requires at least one worker and requester")
            print("Experimental integrated Docker lab: actual exp_fdb_hub, strict MSK, P1 live sessions", flush=True)
        else:
            print("Experimental Hub parity lane: actual exp_fdb_hub (not Stable Hub)", flush=True)
        bridge = "dev-chain" if args.live_chain else "mock-chain"
        print(f"Ports: {', '.join(map(str, EXP_PORTS))}; namespace: {EXP_NAMESPACE}; bridge: {bridge}", flush=True)
        exp_args = [
            "--host", "0.0.0.0" if args.serve_exp_docker_lab else "127.0.0.1",
            "--ports", ",".join(map(str, EXP_PORTS)),
            "--namespace", EXP_NAMESPACE,
            "--cluster-file", str(args.cluster_file),
            "--bridge-backend", bridge,
            # This identifies the Experimental *Hub* network, which also owns
            # the accepted-session Temporal namespace. Chain selection must not
            # move P2-A lifecycle records to "main-computer-dev". The local
            # blockchain is selected independently through the explicit dev
            # deployment manifest, chain ID, and host RPC below.
            "--network-key", "exp-handoff-parity",
            "--network-kind", "experimental",
            "--no-fdb-autostart",
            "--require-multisession-auth",
        ]
        if chain_config is not None:
            exp_args.extend([
                "--chain-id", str(chain_config.chain_id),
                "--chain-rpc-url", chain_config.rpc_url,
                "--dev-chain-deployment-path", str(chain_config.state_path),
                "--enable-smoke-bridge",
            ])
        if args.serve_exp_docker_lab:
            exp_args.extend([
                "--docker", "--docker-request-mode", "live_session_v1",
                "--docker-hub-host", "host.docker.internal",
                "--nodes", str(args.docker_workers + args.docker_requesters),
                "--docker-workers", str(args.docker_workers),
                "--docker-requesters", str(args.docker_requesters),
                "--docker-live-concurrency", str(args.docker_live_concurrency),
                "--docker-live-timeout-seconds", str(args.docker_live_timeout_seconds),
                "--docker-run-timeout-seconds", str(args.docker_run_timeout_seconds),
                "--docker-output-dir", str(args.docker_output_dir),
            ])
            if chain_config is not None:
                exp_args.extend([
                    "--live-chain-smoke",
                    "--live-chain-smoke-state", str(chain_config.state_path),
                    "--live-chain-smoke-rpc", chain_config.rpc_url,
                    "--live-chain-smoke-chain-id", str(chain_config.chain_id),
                ])
        return exp_main(exp_args)

    if args.verify_exp_temporal:
        from tools.exp_hub_lab.experimental_temporal import verify
        result = verify(
            session_id=args.temporal_session_id, cluster_file=args.cluster_file,
            exp_namespace=EXP_NAMESPACE,
            temporal_address=args.temporal_address or __import__("os").environ.get("MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS", ""),
            checkpoint=args.temporal_checkpoint,
        )
        if args.json:
            print(json.dumps(result, sort_keys=True, indent=2))
        else:
            state = "PASS" if result.get("ok") else "FAIL"
            print(f"Experimental Hub P2-A Temporal outbox: {state}")
            if result.get("ok"):
                print(f"  phase: {result.get('phase')}")
                print(f"  checkpoint: {result.get('checkpoint')}")
            else:
                print(f"  error: {result.get('error')}")
        return 0 if result.get("ok") is True else 1

    if args.verify_exp_recovery:
        from tools.exp_hub_lab.experimental_stress import verify_experimental_recovery
        try:
            result = verify_experimental_recovery(hub_urls=EXP_URLS, timeout=args.timeout)
        except (ValueError, RuntimeError, OSError) as exc:
            result = {"ok": False, "mode": "verify-exp-recovery", "error": str(exc)}
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else (
            f"Experimental Hub P1 in-flight recovery: {'PASS' if result.get('ok') else 'FAIL'}\n"
            + "\n".join(f"  {key}: {'PASS' if value else 'FAIL'}"
                         for key, value in (result.get('checks') or {}).items())
            + (f"\n  error: {result['error']}" if result.get('error') else "")
        ))
        return 0 if result.get("ok") is True else 1

    if args.verify_exp_stress:
        from tools.exp_hub_lab.experimental_stress import verify_experimental_stress, render_stress
        try:
            result = verify_experimental_stress(
                hub_urls=EXP_URLS,
                runs=args.verify_exp_stress_runs,
                parallel=args.verify_exp_stress_parallel,
                timeout=args.timeout,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            result = {"ok": False, "mode": "verify-exp-stress", "error": str(exc)}
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else render_stress(result))
        return 0 if result.get("ok") is True else 1

    if args.verify_exp_handoff or args.verify_exp_accounting:
        result = exp_lab.verify_handoff(
            hub_urls=EXP_URLS,
            stress_requests=3,
            failure_indices=(2,) if args.verify_exp_accounting else (),
            funding_credits=100,
            timeout=args.timeout,
            require_multisession_auth=True,
        )
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(exp_lab._render_verify_text(result))
        return 0 if result.get("ok") is True else 1

    # Side-by-side result from the ACTUAL separate servers; normalize only
    # demonstrated handoff assertions, not payment/accounting protocols.
    from tools.stable_hub_lab.run_lab import build_stable_hub_verification_result
    try:
        stable = build_stable_hub_verification_result(
            topology_path=exp_lab.DEFAULT_TOPOLOGY,
            timeout=args.timeout,
        )
    except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
        stable = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        exp = exp_lab.verify_handoff(hub_urls=EXP_URLS, timeout=args.timeout, stress_requests=3,
                                     require_multisession_auth=True)
    except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
        exp = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    exp_proof = dict(exp.get("proof") or {})
    stable_proof = dict(stable.get("proof") or {})
    result = {
        "ok": bool(stable.get("ok") is True and exp.get("ok") is True),
        "stable": {"ok": stable.get("ok") is True, "service": "main_computer.stable_hub", "proof": stable_proof, "error": stable.get("error", "")},
        "experimental": {
            "ok": exp.get("ok") is True,
            "service": "main_computer.exp_fdb_hub",
            "error": exp.get("error", ""),
            "remote_handoff": exp_proof.get("remote_entries_handoff_to_owner") is True,
            "owner_continuation": exp_proof.get("continuations_point_to_owner_hub") is True,
            "requests": exp.get("metrics", {}).get("requests_attempted", 0),
        },
        "scope": "local-cross-hub-handoff-only; financial lifecycle and Temporal not compared",
    }
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else (
        f"Actual dual-runtime handoff: {'PASS' if result['ok'] else 'FAIL'}\n"
        f"  stable: {'PASS' if result['stable']['ok'] else 'FAIL'}\n"
        f"  experimental: {'PASS' if result['experimental']['ok'] else 'FAIL'}\n"
        f"  experimental remote forwarding: {result['experimental']['remote_handoff']}\n"
        f"  experimental owner continuation: {result['experimental']['owner_continuation']}\n"
        f"  scope: {result['scope']}"
    ))
    return 0 if result["ok"] else 1

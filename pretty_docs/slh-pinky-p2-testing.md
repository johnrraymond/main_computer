# SLH Pinky Promise P0–P2 Verification — Windows Harness

**Status:** P0 acceptance blocker, P1 durable organization wallet and read-only Hub balance, P2 direct-TLS private Requester/Worker path. **Not a Pinky Promise end-to-end certificate.** P3 fixed assessment is separately described in [P3 verification](slh-pinky-p3-testing.md); P4 Reserve settlement, P5 feedback, P6 acceptance remain outstanding.

Run from repository root after installing P0, P1, and P2 (and the governing docs patch):

```powershell
python -m pytest -q tests/test_slh_lab_exit_acceptance.py
python -m pytest -q tests/test_slh_organization_wallet.py tests/test_slh_marketplace_automatic.py tests/test_slh_scheduler_policy.py
python -m pytest -q tests/test_slh_private_requests.py tests/test_slh_private_execution_integration.py
python -m pytest -q tests/test_slh_operational_service.py
# Extended live gateway regression, run separately:
python -m pytest -q tests/test_slh_managed_gateway.py
```

Or execute the accompanying source-controlled harness:

```powershell
.\tools\slh_seh_lab\verify_pinky_p0_p1_p2.ps1
```

The longer TLS/SEH gateway regression is optional because it may require more time and Windows network behavior:

```powershell
.\tools\slh_seh_lab\verify_pinky_p0_p1_p2.ps1 -TransportRegression
```

Optional real-process lab smoke (may take longer, requires the local smoke topology and test prerequisites):

```powershell
python -m tools.slh_seh_lab.run_lab verify-local --json
python -m tools.slh_seh_lab.run_lab lab-exit --exit-profile smoke --json
```

Optional full Windows exit, **not an expected PASS** until all six required gaps are implemented (includes at least ten 64-Worker stress repetitions and >= 3,600 second soak):

```powershell
python -m tools.slh_seh_lab.run_lab lab-exit --exit-profile full --exit-repeats 10 --exit-soak-seconds 3600 --json
```

The exit runner intentionally returns **NOT_ACCEPTED** and a nonzero process code while mandatory requirements remain absent. Check `runtime/reports/slh-seh-lab/<run-id>/report.json` and the nested `exit-evidence/exit-report.json`, not process exit alone. The harness accepts **NOT_ACCEPTED** as a truthful exit state but never represents it as laboratory acceptance.

**P2 contract:** Requester session with `role=requester`, `POST /v1/work` with `{request_id,model,work}`, and `GET /v1/work/{id}` using the same requester. Work is currently restricted to private chat inputs with a matching Worker model. The execution connection uses a separate `slh-private-v1` message namespace, not a Stable Hub paid authorization. Success is durably committed as a metadata-only terminal record before the HTTP result is returned. A repeated request ID never reexecutes; uncertain dispatched work is `OUTCOME_UNKNOWN`, requiring explicit reconciliation. No prompt or response body is written to SQLite.

**Current limits:** private execution is enabled only where the Organization Service's SEH forwarding is disabled, pending a separately validated SEH private E2E policy. P2 alone did not assess 0.1 credits; P3 adds the obligation journal separately. Neither patch transfers Reserve funds, makes provisional results spendable, or retroactively assesses pre-P3 requests. `billing_policy_id=p2-pre-assessment` identifies legacy non-billable P2 admissions. The representative-model-output laboratory exit blocker remains until actual secure-model computation is demonstrated independently of synthetic test callbacks.

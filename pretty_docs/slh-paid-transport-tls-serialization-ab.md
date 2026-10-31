# SLH paid Worker TLS I/O serialization: production default and A/B regression

The Windows direct-TLS and SEH-mediated probes lost the Worker during paid-result
transmission while the idle-only baselines passed. Both report Worker-side
`SSLEOFError`; the Organization Service reports an abnormal WebSocket close.
The initiating TCP close has **not** been proved.

Windows A/B evidence (2026-10-30): the former baseline lost the direct Worker
in cycle 1 and the SEH Worker in cycle 6 of 32, while serialized I/O completed
32/32 on **both** paths with the same paid-work protocol. Both changes in the
experimental arm (mutex serialization and 100 ms poll) are promoted together,
as the experiment did not isolate their separate contributions.

The production Worker now **defaults to serialized TLS socket I/O**: each
`recv`, `sendall`, or `close` uses the same per-client mutex. The receive timeout
remains 100 ms in this mode so outgoing paid results are not indefinitely held
behind an idle receive. This is not a whole-message lock. The original
`baseline` remains available only as an explicit A/B control via
`--tls-io-mode baseline` in `diagnose-paid-transport`.

The controlled comparison holds the Organization Service, paid-job protocol,
Stable Hub, synthetic ledger, TLS identity, and per-mode SEH topology constant.
It changes only the Worker client's socket I/O serialization. Check all
completion and worker-retention gates, not merely settlement success.

Verify production-default behavior and the explicit regression control on Windows:

```powershell
python -m tools.slh_seh_lab.run_lab diagnose-paid-transport --transport both --cycles 32 --idle-seconds 0.5 --json
python -m tools.slh_seh_lab.run_lab stress-integrated --workers 64 --requests 64 --concurrency 8 --seed 1729 --causal-probe --json
# Opt-in comparison only; expected to reproduce the old Windows failure:
python -m tools.slh_seh_lab.run_lab diagnose-paid-transport --transport both --cycles 32 --idle-seconds 0.5 --tls-io-mode baseline --json
```

Each arm uses fresh disposable SLH organizational state and independent local
Stable Hub instances. Results record `tls_io_mode` in each run and per-path
trace. The existing Windows differential experiment is evidence of a working
correction, not definitive proof of the exact internal OpenSSL failure. Verify
full 64-Worker retention, settlement and lifecycle convergence on Windows. If
those tests still fail, use the collected close-origin evidence to identify any
remaining independent failure before modifying additional transport paths.

A separate Windows forensic logging issue is fixed: querying
`websockets.sync.Connection.remote_address` after the socket closes can raise
`OSError: [WinError 10038]`. This is a diagnostic handler error; it is not an
explanation for the direct-TLS failure.

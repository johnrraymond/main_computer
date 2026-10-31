# SLH Paid-Stress Preflight Causal Witness

The `stress-integrated` lane executes a single real-Worker paid transaction before
starting the requested fleet. A failure of that original Worker is **not** a
64-Worker capacity failure. The runner records the original Worker's state
immediately before and immediately after the preliminary paid cycle.

The first two events in the disposable `integrated-stress-events.json` are
`preliminary_paid_worker_checkpoint`, with `stage` set to
`before_preliminary_paid_job` and `after_preliminary_paid_job`. A subsequent
integrated-stress failure preserves those checkpoints in the trace.

If the Worker disappears, the early failure path retrieves the authenticated
Organization Service `connection-diagnostics` for that device, joins the
Worker/client and adapter timelines, and writes the result to the event trace.
The command-line error remains brief and points to the trace. No raw exception
messages, authorization, signing keys, TLS contents, or job payloads are logged.

A Worker that is registered but reserved by an active job is *not* classified
as disconnected merely because `available` is false. The failing condition
requires a missing registration or a dead client.

Interpretation: a failure before the fleet is established cannot establish a
large-fleet concurrency defect. Compare gateway close direction, adapter first
close, and Worker socket events in the same trace before choosing a transport
repair. Stable Hub settlement remains a synthetic lab-only transaction.

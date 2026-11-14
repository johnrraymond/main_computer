# LMS Ownership of SEH Management and Deployment

**Status:** Governing architecture and implementation/acceptance contract, November 13, 2026. The LMS ownership cutover is implemented by the accompanying code patch against `main_computer_test-20261113-111423.zip`; live Windows and Coolify acceptance remain separate. This specification supersedes earlier descriptions of ORG-SLH as the executor of Coolify or SEH deployment operations.

Related contracts: [LMS-SLH Identity Ownership](lms-slh-identity-ownership.md), [MCEL Identity Selection and Transparent Authorization](mcel-identity-selection-and-authorization.md), [SLH Backend Current Capabilities](slh-backend-current-capabilities.md), and [SEH Operator Harness](seh-operator-harness.md).

## Governing rule

**The Local Management Service (LMS-SLH, abbreviated LMS) is the sole local execution owner of SEH management.** It reads and updates verified Mother private identity state through existing Mother authority/compare-and-swap operations; resolves registered Coolify controllers and SEH deployment bindings; invokes the established SEH harness; serializes and tracks operations; verifies Coolify results; and owns recovery and durable operation evidence. It is not a competing identity-state authority or a replacement for the harness.

**The ORG-SLH is the authentication and authorization gateway for requests originating from Network SLH.** Following successful device proof, enrollment/role verification, and operation-specific authorization, it forwards a bounded request to the **LMS on the same remote host**. It does not read Coolify private state, keep a parallel Coolify registry, start a second deployment controller, or execute the harness itself.

**MCEL is a client.** It selects targets and identities, presents a coherent SEH Lifecycle notebook, requests operations, and displays verified receipts. It is not a Coolify client, deployment supervisor, or source of authority. **Remote SEH is strictly excluded** from this management interface; its administrative tunnel remains limited to the accepted identity-management operations.

The ownership rule applies to **both reads and mutations**, including Coolify controller registration/selection, deployment preflight, deploy, undeploy, status, diagnostics, recovery, and operation tracking. Deployment is not merely delegated by LMS: it remains **owned by LMS for its entire lifecycle**.

## Two ingress paths, one execution implementation

| Dimension | This Computer | Network SLH |
| --- | --- | --- |
| Request path | MCEL → local LMS | MCEL → direct, pinned TLS → target ORG-SLH → target machine's LMS |
| Trust boundary | Operating-system/user and strictly local management boundary; **no ORG-SLH administrator login** | ORG-SLH cryptographic device authentication, target-scoped role and operation authorization, then trusted local forwarding |
| Coolify identity-state reader | Local LMS | Remote machine's LMS; never ORG-SLH's own duplicate reader |
| Deployment executor | Local LMS invokes its SEH harness | Remote machine's LMS invokes **its** SEH harness |
| Durable operation owner | Local LMS | Remote machine's LMS, including after the network connection drops |
| Credential exposure | No Coolify token/private Mother state in MCEL/browser | No Coolify token/private Mother state in MCEL, remote browser, or ORG-SLH's HTTP response |

**This Computer:** Local inspection of saved Coolify controllers, selection, deployment status, and ordinary local management do not require a selected ORG-SLH signing identity or its administrator session. The LMS must still authenticate/authorize the *local caller at the OS/local-service boundary*, bind only to protected local interfaces, enforce origin/CSRF defenses where applicable, and apply explicit confirmation, compare-and-swap, and mutation policy. **No ORG-SLH login** is not **public or unauthenticated network access**. An identity may still be selected for independent identity-specific operations; its absence must not artificially block local Coolify inspection.

**Network SLH:** A selected ordinary LMS signing identity may be needed to prove authority to the **remote ORG-SLH** when a protected operation is requested, with no manual Login/Logout UI. That ORG-SLH verifies the principal, active enrollment, role, target binding, and permission for the *specific* operation. Only after successful authorization does it forward to the remote LMS. Neither an MCEL role badge nor identity selection alone authorizes a remote operation. ORG-SLH never forwards its own unrestricted administrative bearer or any raw Coolify token. A rejected request never reaches LMS.

**Remote SEH:** Does not offer Coolify discovery, SEH deploy/undeploy, root authorization, host lifecycle, or unrestricted organization management. The restricted relay identity listener cannot be reused as the forwarding path above.

## One bounded LMS management contract

Both ingress paths must invoke the **same LMS-owned service operations**, using the same verification, deployment-state, and result contracts:

| Operation | LMS action | Mutation |
| --- | --- | --- |
| `inspect` | Read verified Mother registry and effective canonical deployment/operation state; return sanitized controller names, selected binding, generation, and meaningful status | No |
| `register-controller` | Validate and register a Coolify identity in verified Mother private state; do not implicitly deploy/select | Identity-state CAS only |
| `select-controller` | Bind a saved controller to the canonical deployment; refuse changes that would move an owned/active deployment | Identity-state CAS only |
| `preflight-deploy` / `preflight-undeploy` | Invoke the existing harness in read-only mode; verify exact target, current state, source, key/ownership, and safety gates | No |
| `deploy` / `undeploy` | Invoke **the same harness** with explicit approval after fresh preflight; retain verified resource UUID, commit or reconcile governed durable state | Yes |
| `operation-status` / `diagnostic` / `reconcile` | Read durable operation receipts, check remote truth, and expose safe failure/continuation actions | Read-only unless an explicitly approved reconcile mutation |

These are **semantic operations**, not permission for a generic arbitrary shell command or filesystem path. The LMS resolves the local Mother state root, controller credentials, application UUID, and harness configuration from its own protected state. MCEL and ORG-SLH may not supply a replacement state-root path, executable name, raw CLI arguments, Coolify bearer, or arbitrary destination. Existing command-line harnesses remain usable for explicit operator maintenance and lab work; they are the execution machinery **under LMS ownership when called by MCEL**, not a second production control plane.

For authenticated network forwarding, ORG-SLH should send only a fixed operation identifier, validated arguments, a target-machine-bound authorized principal/permission assertion, an operation idempotency key, and any required confirmation/generation/digest. The **LMS must accept that assertion only from the local, authenticated ORG-SLH process/channel**, never from caller-supplied HTTP headers or directly from the network. The forwarding channel must have process/service identity, integrity, replay protection, and a narrow allowlist; loopback addressing alone is not authorization. LMS can enforce further local mutation policy without restarting ORG-SLH authentication.

## Deployment ownership, durability and consequences

A deployment transaction is **not** complete because a web request returned or Coolify accepted a job. The LMS manages the complete chain:

1. Read the selected controller and canonical owned application, together with Mother generation and prior harness receipts.
2. Run a read-only ownership/source/trust/quiescence preflight; reject stale selections or a UUID mismatch. A legitimate first deployment and an explicit redeploy have distinct preconditions.
3. Record the operator's authorized request and durable, unique operation identifier **before any consequential remote mutation**; serialize against concurrent deployment/recovery and apply the existing harness approval gates.
4. Call the SEH harness, using LMS-local resolved resources and secrets. Never invent a second Coolify deploy/undeploy implementation in ORG-SLH or MCEL.
5. Preserve the Coolify application UUID, controlled identity-state changes, quiescence/settlement evidence, and durable recovery receipts. Do not delete a Coolify controller merely because its SEH was undeployed.
6. Query actual Coolify/deployment status and, separately, public SEH health and authenticated tunnel readiness. Distinguish `QUEUED_NOT_VERIFIED`, `INCOMPLETE`, `VERIFIED`, `REFUSED`, and pending recovery; these are **required semantic distinctions**, not a claim those exact strings are today's API values.
7. Make the operation status re-readable by its ID even if MCEL closes, the Network SLH tunnel disconnects, or an ORG-SLH process restarts. A reconnect must query the original receipt, **not create another deployment attempt**.

Network authorization and caller attribution must be preserved in the durable LMS operation record, but the operator's short-lived remote bearer/credential must not be persisted. A target switch or signer deselection prevents new unauthorized work but must not cancel/reattribute an already accepted LMS operation. An LMS crash/restart must preserve an unconfirmed attempt as an inspectable receipt, not silently replay it. Before any new, separately authorized Coolify mutation, the existing SEH harness must perform its normal recovery, exact-UUID, topology, and ownership checks; lost acknowledgments and prior undeploy receipts do not authorize duplicate destructive calls. Unknown remote state must not be asserted successful.

**Deployment and connection are separate lifecycles.** Deploying an SEH does not prove an authenticated SLH tunnel. Stopping/disconnecting a tunnel does not undeploy Coolify. An undeploy preflight must enforce the established connection/quiescence and in-flight-work rules; absence of a tunnel alone is not proof that all admitted work is settled. The source/ownership record, certificate/trust checks, and explicit operator confirmation remain required.

## Durable asynchronous operations and reconnect-visible LMS busy state — November 13 extension

**Governing rule:** The LMS is the sole owner of the exclusive SEH management operation. Its browser caller, administrator ticket, and ORG-SLH forwarding connection are *not* the operation owner. A Deploy/Undeploy request is accepted and durably journaled **before** LMS starts a separate execution worker. The submission HTTP response returns promptly with a stable `operation_id` and `ACCEPTED` status; it does not wait for the SEH harness or assert that a Coolify job or network relay is verified. The LMS journal holds its lock only for admission, state transition, or receipt reads/writes—not throughout the harness call. The single-request loopback control listener can therefore answer other management reads and status requests while the harness is running.

**Every local/remote management connection must obtain an operation-state snapshot before permitting conflicting work.** After a Network SLH administrator authenticates, ORG-SLH forwards the same bounded status request to *that machine's* LMS. The snapshot is not a browser-session variable or a cache in ORG-SLH; it is read from the resident LMS operation journal. MCEL starts with a status check, then polls while connected. Losing a browser, switching This Computer ↔ Network SLH, or disconnecting a TLS session **does not cancel, resume, or duplicate** execution. Another connected browser targeting the same LMS displays the same operation ID and stage. A different machine is an entirely different LMS and has independent state.

| LMS state | Snapshot and MCEL behavior | New consequential management requests |
| --- | --- | --- |
| `IDLE` | No live exclusive operation; regular workspace enabled. An optional `last_operation` may identify a terminal `INCOMPLETE` receipt, available as a nonblocking, reopenable notice | Allowed only through the SEH harness's normal preflight, recovery receipts, exact-UUID and mutation guards |
| `BUSY` | Current `operation_id`, operation type, stage, phase, and `cancelable` exposed; show a dismissible operation dialog over the management workspace; after dismissal, retain a visible, reopenable operation indicator while navigation and non-conflicting work remain usable | Refused by LMS's exclusive admission gate even if a second browser missed the modal |
| `UNAVAILABLE` (client observation) | Transport or status projection could not be verified; display a nonblocking, retryable status notice; never infer that the LMS is idle or bypass its server-side mutation admission gate | Do not infer idle from missing network data |

**Status and cancellation contract (bounded endpoints, not generic command execution):**

- `GET /v1/local/seh/deployment/operations` returns `main-computer.seh-management-operation-snapshot.v1` with `state` (`IDLE` or `BUSY`), `busy`, an optional live `operation`, and optional `last_operation` for the most recent incomplete terminal receipt (`operation_id`, `action`, `stage`, `phase`, and `cancelable`). `GET /v1/local/seh/deployment/operations/<id>` returns the persistent receipt. Both may be called during harness execution.
- `POST /v1/local/seh/deployment/operations/<id>/cancel` requests cancellation **only while durable admission is ACCEPTED and harness execution has not begun**. It records `CANCELED` before dispatch; the worker checks the journal before executing. Once the harness reaches `RUNNING`, cancellation is refused (`SEH_CANCEL_UNSAFE`) because blindly killing a Coolify mutation would create uncertain ownership state. The UI must not display an enabled Cancel control at that stage. Future safe harness checkpoints may expand cancelability, but it may never be guessed in the browser.
- The direct Network SLH routes have the same `/operations` and `/operations/<id>/cancel` suffixes behind pinned TLS and ORG-SLH administrator authorization; ORG-SLH forwards through its existing signed, replay-protected LMS IPC. The restricted Remote SEH relay has **no** management-state or cancellation route.
- A mutation is durably admitted after validating the Mother generation and operation confirmation. Before the irreversible harness boundary, LMS verifies generation again. A repeated operation ID with the same principal and parameters returns the existing receipt state; it must not run twice. A distinct request is refused while an exclusive live worker exists; historical failures do not hold a global LMS lock. The SEH harness continues to refuse unsafe remote mutations based on its own recovery receipts, mutation lock, topology and exact-UUID checks. Controller registration/selection still cannot bypass *live* exclusivity.

**Immediate submission handoff:** On a confirmed Deploy/Undeploy click, MCEL MUST show the management dialog synchronously, before any LMS operation ID, preflight, or HTTP acknowledgement exists. It starts in `SUBMITTING` presentation state, which is explicitly *not* evidence that LMS accepted the command. Once the backend acknowledges an operation ID, the dialog changes to the LMS-owned state, and the same ID is monitored through completion. If the submission fails or the acknowledgement is lost, the dialog preserves that failure and offers a retry of **observation** only; it must not silently repeat a consequential mutation. An idle status poll cannot hide an in-flight submission or its error. No navigation or page refresh is required for the dialog to learn that an operation has completed or can be dismissed. Dismiss is always available even during submission, and only closes the UI presentation.

**Visible operation results:** The dialog includes a bounded, independently scrollable result area. It records the local dispatch, LMS acknowledgement, actual LMS phase/stage observations, **the actual stdout and stderr produced by the fixed SEH harness process**, and the allowlisted final receipt fields (status, error code/type, application UUID, remote mutation state). LMS captures both streams from a controlled child invocation of the installed `seh_mutate_harness.py`, running under the LMS Python environment; it never runs caller-selected programs or shell commands. The CLI emits a private result receipt separately from its stdout and stderr. The LMS strips known Coolify controller credentials and standard bearer/secret patterns before persisting or forwarding output, suppresses oversized lines, and retains only the latest bounded output entries with monotonic sequence numbers. The status snapshot and specific operation receipt expose this *redacted* evidence through the existing local or authorized Network SLH management route. MCEL renders those entries as text, with stdout/stderr labels, and deduplicates them across polls. Reconnecting to an active operation reconstructs the retained output without restarting execution; older entries beyond the bound are intentionally unavailable. The UI must not fabricate command output, disclose raw credentials or claim queued Coolify work is runtime verified.

**Asynchronous preflight handoff:** Explicit Preview buttons remain read-only operations, but automatic preflight for a requested Deploy or Undeploy runs **inside the admitted LMS worker**, not synchronously in MCEL's HTTP submission path. LMS validates the Mother generation before admission, performs a read-only harness preflight with captured stdout/stderr, and refuses the mutation if it did not produce an admissible plan (or if undeployment is not ready). It validates the generation again before running the actual mutating harness. A preflight failure becomes a terminal `REFUSED` operation receipt with its diagnostic output; it does not assert that any remote mutation occurred. This prevents a long preflight from blocking LMS's single-request listener or obscuring the actual failure behind a generic client timeout.

The modal is a **general workspace overlay**, not an SEH notebook's pending button. It covers conflicting management controls but leaves the target navigation usable so operators can switch perspectives. It displays only LMS-provided fields, never private identity state, Coolify tokens, command-line arguments, or unverified percentage progress. A completed harness receipt can be displayed as a terminal outcome, while runtime verification of SEH health remains a separate operation. The user can **always dismiss the overlay**, including while a harness is running, while observation is unavailable, or when recovery is required. Dismissal is presentation-only: it never cancels a deployment, clears an LMS receipt, or releases the server-side exclusivity gate. An unobtrusive persistent indicator must allow reopening current LMS state without reissuing the operation. A separate **Cancel operation** action is shown only when LMS reports `cancelable: true`; it never substitutes for Dismiss. The dialog is centered within the visible management workspace with responsive width/height and internal scrolling, rather than within the whole scrollable document. An incomplete historical receipt and status-unavailable observations must not leave unrelated controls or navigation inert. A completed harness receipt can be displayed as a terminal outcome, while runtime verification of SEH health remains a separate operation.

**Process restart has a different guarantee from connection reconnect.** A browser or ORG-SLH reconnect does not stop the running LMS worker. A hard LMS termination can interrupt an in-flight action. On restart, a historical `ACCEPTED`, `RUNNING`, or legacy `RECOVERY_REQUIRED` receipt is normalized to terminal `RECORDED` with `result.status: INCOMPLETE` and `remote_mutation: UNKNOWN`; the prior stage remains recorded for audit. The snapshot reports `IDLE`, `busy: false`, and an optional `last_operation` rather than inventing a running worker or holding an indefinite lock. Replaying that operation ID **never** invokes the harness again. A new explicitly authorized operation is admitted only when no live worker is active, and then follows the existing harness recovery/preflight checks; the journal does not certify the remote state or bypass those checks. No automatic retry, SQL queue, distributed scheduler, or second deployment manager is introduced.

**Required acceptance scenario:** start Deploy from This Computer; while the harness is deliberately blocked, authenticate to the *same* machine through Network SLH; observe the *same* operation ID and stage through ORG-SLH forwarding; verify unauthorized clients cannot read it; confirm conflicting Deploy/Undeploy is refused; release harness and observe a durable terminal receipt without a duplicate mutation. Repeat in the reverse direction, on browser refresh, and with a previously interrupted receipt that must be inspectable but not busy. The stage is never derived from a browser timer.

## Implementation gap in the November 13 snapshot

**[IMPLEMENTED IN LMS OWNERSHIP CUTOVER PATCH]** `main_computer/mcel_slh_seh_deployment.py` dispatches local Coolify/SEH work directly to the loopback LMS endpoint without an ORG bearer or selected identity. `main_computer/slh/local_service.py` constructs the only `SLHSEHDeploymentManager`. `main_computer/slh/organization_service.py` authenticates direct Network SLH administrators and forwards fixed operations to LMS through `seh_lms_ipc.py`; it does not instantiate a second manager. The forwarding message includes the verified remote device identity in an HMAC signed assertion with an expiring timestamp and replay nonce. LMS writes scoped operation receipts with the attributed principal to `runtime/state/seh-operations/<network>/` before invoking the existing harness.

**[ACCEPTANCE STILL REQUIRED]** The patch has isolated backend, forwarding, and MCEL browser test coverage. It has not been accepted against a live Windows resident LMS/ORG-SLH pair or production Coolify. For a queued build, the durable receipt records successful *submission*, not verified live SEH readiness. If execution or its response is uncertain, the journal records a terminal `INCOMPLETE` result and never blindly repeats the same operation ID. The existing harness—not a parallel LMS recovery gate—owns remote-effect verification and safe handling of any new explicitly requested mutation. No new distributed scheduler or automatic recovery worker is introduced.

This is a documentation contract. Neither the manager relocation, trusted forwarding channel, nor durable operation ownership is certified merely by updating these files.

## Acceptance gates

- **Local/no-login:** With LMS running but ORG-SLH stopped, unconfigured, or lacking an administrator session, This Computer can inspect the saved Coolify controller names and deployment state via LMS under local machine authorization. No browser/ORG bearer, challenge, or Worker Bank read is required. Local mutations still demand the applicable local confirmation and safety checks.
- **Remote/authorized:** A verified Network SLH administrator request traverses direct pinned TLS → target ORG-SLH authorization → that host's LMS. The exact selected remote controller and application are used; no local fallback.
- **Remote/denied:** Wrong identity, unenrolled/revoked identity, nonadministrator role, wrong TLS pin, or missing required permission fails **before LMS invocation**. There is no implicit bootstrap or privilege grant.
- **Relay refusal:** Remote SEH cannot access the forwarded LMS deployment endpoints, even with an otherwise valid remote-SEH identity-management session.
- **One owner:** Equivalent direct and forwarded requests hit the same LMS service instance, state root, harness adapter, concurrency lock, and durable operation journal. No ORG-SLH-owned competing deployment manager or Coolify credentials.
- **Transactional:** Concurrent deploy/undeploy requests, stale Mother generations, conflicting application UUIDs, incomplete quiescence, uncertain DELETE acknowledgments, and recovery cases are handled without double mutation or false success.
- **Disconnect/restart:** A queued or executing job remains attributable to its original request when MCEL/Network SLH disconnects; status is recovered by operation ID without automatically rerunning the harness.
- **Data hygiene:** Controller API tokens, Mother private state, P-256 private keys, local root capability, and short-lived network bearers do not enter browser-visible responses, audit messages, or protocol forwarding payloads.

All acceptance evidence must distinguish isolated/mocked tests from a verified live Coolify deployment and real end-to-end network access.


### Canonical deployment-to-activation operation (2026-11-13)

The LMS owns one durable, observable operation for both SEH application
registration and activation. MCEL's **Deploy** action is valid only after a
verified intentional undeploy. LMS runs the canonical deploy preflight, the
existing deploy harness, verifies the new canonical application UUID from its
local operational state, then runs the existing activation preflight and
activation harness under the same operation ID. A queued Coolify build is not
reported as a verified connection. If readback cannot establish the exact UUID,
LMS retains the queued result and does not initiate activation.

For an already registered canonical application, MCEL offers **Preflight
Activate** and **Activate / Resume**. Those actions use the same direct LMS or
authenticated Network SLH-to-LMS management contract, with no replacement
application. Even a stale Deploy request against a registered application is
resolved by LMS to activation, never to a second Coolify application creation.
If the activation preflight refuses, its real harness output and result are
recorded, not concealed by a generic MCEL submission failure.

Only the LMS may execute these operations; remote ORG-SLH authenticates and
forwards. The existing harness remains responsible for administrator signing,
Worker setup, tunnel verification, and safe runtime recovery. The immediate
MCEL modal opens on the operator click and follows the same durable operation
receipt and bounded stdout/stderr across reconnects. No manual PowerShell
activation is part of the intended operator workflow.

### MCEL modal subprogram presentation (SLH integration)

The MCEL Lab–proven `McelSemanticModalSubprogram` now owns the SLH operation dialog’s geometry, region budgets, visible-workspace positioning, overflow and presentation lifecycle. The application declares the header, details, output and actions region contracts; it does not resize the dialog in response to LMS stages or harness output. The operation monitor retains all LMS state handling, immediate submission handoff, cancellation admission and durable stdout/stderr reconstruction. Local and Network SLH observations use the same modal presentation. For the exact geometry and oracle, see `mcel-modal-subprogram-lab-contract.md`.

# SEH Harness — Operations × Functionality × Modules

**Status:** user-facing operations and implementation design; **remote, identity-state-driven lifecycle is specified, not yet implemented**. Reviewed against the Main Computer repository snapshot of 2026-10-28.

**Scope:** deploy, operate, observe, verify, recover, and retire a Secure External Hub (SEH) through Coolify, using the same shared private infrastructure identity source and guarded mutation approach as Mother, FoundationDB Control, and Hub Control. The Secure Local Hub (SLH) service and its future administration application are consumers of **one** SEH management backend—not independent deployers.

Related specifications: [SEH Coolify Harness](seh-secure-external-hub-coolify-harness.md), [Real SEH Runtime and Acceptance](seh-real-relay-deployment-and-acceptance.md), [Automatic Local Coolify](slh-seh-automatic-local-coolify.md), [SLH Authentication](slh-identity-protocol-spec.md), [Hub O/F/M](hub-o-f-m.md), [FDB O/F/M](fdb-o-f-m.md), [Mother O/F/M](mother-o-f-m.md).

## 1. What the administrator does

An SLH administrator registers an SEH deployment target once, selects an existing Coolify controller and desired public domain, reviews a frozen plan, explicitly authorizes mutation, and lets the harness deploy and verify the relay. From then on the administrator can check its health, upgrade it, disable its use, or retire it. The SLH automatically connects to an **enabled, configured** SEH when the gateway starts and reconnects after a tunnel failure. An SEH provides remote transport only; **it never becomes a device-enrollment authority, a marketplace wallet, or an organizational scheduler**.

The intended normal lifecycle is:

```text
inspect/list  →  register target  →  plan  →  preflight
                                             ↓
                                  explicit approval / do
                                             ↓
                           Coolify deploy + runtime proof
                                             ↓
                         verified record + SLH configuration
                                             ↓
                      status / upgrade / disable / retire
```

For mutation, the harness owns preparation, execution, resumption, and finalization. A future SLH UI calls those same backend operations with the administrator's authenticated authorization; it must not reimplement Coolify requests.

### 1.1 Current reality versus proposed operator surface

| Surface | Inspected current implementation | This document specifies next |
|---|---|---|
| `python -m tools.seh_harness` | `profiles`, `contracts`, `probe-hub`, `plan`, `preflight`, `verify-coolify`, `deploy`, `start`, `verify-local`; `--target` JSON; remote token via `SLH_COOLIFY_TOKEN`; explicit `--execute` and matching approvals for mutation | Shared-identity-backed `list`, `register`, `inspect`, `plan`, `preflight`, `deploy`, `verify`, `reconcile`, `upgrade`, `disable`, `enable`, `retire`, and `recover` semantics; public command spellings remain **proposed**, not runnable |
| `main_computer/slh/coolify.py` | Typed `SEHTarget`, pinned-image Compose, plan digest, Coolify read-only preflight, guarded **create** and **start**. Deployment receipt is *not readiness*. Existing-service update is not implemented in that adapter | One lifecycle control backend with observed/accepted reconciliation, update, health proof, recovery, and retirement |
| `tools.slh_seh_lab.run_lab` | Local lab, actual Coolify-managed loopback `verify-deployed`, paid smoke-credit cycle, automatic connection/reconnection checks | Optional remote SEH target with public HTTPS/WSS and full paid-work acceptance; no substitute in a remote verification run |
| `deploy-local-coolify` | Reuses Website Builder's local Coolify bootstrap and token; creates/reconciles local service without operator-provided API key | Preserve this **local-only** path; remote controllers resolve their own existing credentials through shared infrastructure identity |
| SLH gateway | `seh-connection.json` and supervised outbound SEH connection; service key remains local | Backend applies an approved SEH route after verification; no manual connector invocation or duplicate primary tunnel |

**Do not confuse** the existing `tools.seh_harness deploy` (guarded create of a service from a standalone target file) with the planned identity-driven, end-to-end accepted deployment operation. The local Coolify lab proves deployment on the operator's machine; it does **not** confer production remote mutation authority.

## 2. Authority and state ownership

```text
runtime/state/mother/identity.private.yaml   (shared, verified infrastructure input)
    networks.<network>.coolify.controllers.<controller-id>   [existing]
    networks.<network>.sehs.<seh-id>                        [PROPOSED]
                    │             │
     deployment placement          └─ logical SEH registry
                    ↓
            SEH Control backend  ← CLI / SLH service / future admin UI
                    │
       frozen plan + explicit mutation authorization
                    ↓
           Coolify service / remote SEH
                    ↑
       outbound signed SLH connection (SLH-owned key)
```

**Authority invariants:**

1. **Shared infrastructure, independent control.** SEH Control reads the canonical private identity input, like Hub and FDB Control. It does not import `tools.mother.*`, call Mother lifecycle commands as deployment steps, claim Mother chain authority, or own Hub/FDB state. Updating the shared file must use the established verified private-state generation/replace contract, not ad hoc YAML editing.
2. **Separate registry and observation.** A logical SEH target and its approved desired state live in the shared private identity generation. An observed Coolify service UUID, deployment result, endpoint response, and tunnel status are evidence, **not** accepted state until explicitly reconciled and committed. An ambiguous POST response must trigger inspection rather than a blind retry.
3. **Least privilege.** The Coolify controller credential is resolved only when needed by the authorized adapter. No Coolify token is placed in Compose, emitted to Pretty Docs, included in reports, or returned to the UI. The SLH P-256 private relay-signing key remains in its SLH state directory (`seh-service-signing-key.pem`); only the SLH's **public** signing key can be supplied to SEH.
4. **No trust delegation.** The SEH forwards opaque protected streams. Remote Known Devices and individual administrators still authenticate to the SLH. Coolify deployment authority cannot enroll workers or impersonate the SLH.
5. **Network scope is infrastructure scope.** A `mainnet`, `testnet`, `test`, or `dev` registry namespace selects operator placement and policy; it does not bind an SEH to a marketplace wallet or make the SEH a Stable Hub. An organization may register multiple SEHs.
6. **No implicit production promotion.** Loopback `ws://127.0.0.1` is laboratory-only; remote SEH endpoints require externally verified HTTPS/WSS and valid end-to-end authentication.

### 2.1 Proposed shared private identity extension

Illustrative **design data**, not a ready-to-install Mother private-state generation:

```yaml
kind: main_computer.mother.private_state.v1
schema_version: 1
networks:
  mainnet:
    coolify:
      controllers:
        coolify-a:  # existing controller record; retain its canonical fields
          url: https://coolify.example.org
          project_uuid: example-project
          server_uuid: example-server
          api_token_env: COOLIFY_A_API_TOKEN
    sehs:  # proposed additive collection
      company-primary:
        organization_id: company
        slh_id: company-slh
        controller_id: coolify-a
        environment_uuid: example-environment
        public_origin: https://seh.example.org
        image: registry.example.org/main-computer/seh
        image_digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
        slh_public_key_ref: slh://company/company-slh/seh-service-public
        service_uuid: null
        enabled: true
        desired_lifecycle: reserved
```

All shown addresses and IDs are placeholders. The document must preserve pre-existing network fields, controller entries, and unrelated resources. `slh_public_key_ref` is a **proposed reference**, not an implemented resolver: the final module must securely resolve the SLH public key and bind it to the exact organization/SLH pair; the private key remains local. Controller secret references may already be embedded in the shared private identity under the existing controller contract. The `sehs` collection itself must never introduce a duplicate token or wallet secret.

The existing Mother private-state reader requires exactly the **top-level** keys `kind`, `networks`, and `schema_version`, and canonical YAML with metadata/recovery bindings. The new `sehs` field belongs under the chosen `networks.<network>`; its legality under all downstream network-specific validators, canonicalizers, projection writers, and successor paths must be tested **before** writing an actual generation. The file's visible path is **not** permission to edit it in place.

### 2.2 State machine

| State | Meaning | Owner |
|---|---|---|
| `reserved` | Unique logical SEH ID and target placement committed; no deployment proof | Verified shared identity generation |
| `planned` | Frozen desired configuration, source binding, image digest, and approval hash produced; read-only | SEH Control run/evidence |
| `deploying` | Authorized mutation in progress; exact Coolify operation receipt retained | SEH Control run/evidence |
| `observed_healthy` | Coolify resource and SEH health observed; connectivity may still be absent | SEH Control observation |
| `verified` | Required endpoint, identity, tunnel, and relay proofs passed for the *selected* deployment | SEH Control evidence; accepted record advanced only after finalization |
| `degraded` | A previously verified SEH is no longer healthy/reachable/connected | Observation; does not erase prior accepted deployment |
| `disabled` | SLH must not establish new connections to this target | Accepted configuration + SLH connection policy |
| `retired` | Explicitly authorized removal/retention decision completed and verified | Accepted registry with historical evidence |

A service can be `healthy` and still not be usable: `/healthz` establishes a running SEH process; `/readyz` reports a connected SLH; a full remote acceptance requires the correct identity, externally reachable TLS/WSS, and a real relayed session. Do not infer those results from one another. A disconnected persistent organizational tunnel must be reported separately from an isolated lab-channel PASS.

## 3. Operations × functionality × module matrix

**Legend:** `E` existing code path / `P` proposed module or behavior / `E→P` existing foundation to be refactored. Columns `F` identify the ordered functionality stages; `M` identify their owning implementation modules (catalog in §4). The identifiers are design/traceability IDs, **not present CLI or Python symbols**.

| Operation (`O`) | Ordered functionality (`F`) | Ordered owning modules (`M`) | Proof of completion | Status |
|---|---|---|---|---|
| `SEH-O01 inspect/list` | F01 load/bind shared identity → F02 enumerate registered targets → F03 observe Coolify/SEH read-only → F04 compare observed vs accepted | M02 → M03 → M05 → M10 | Precise target list, accepted generation, per-target observed state; no mutation | P |
| `SEH-O02 register/reserve` | F01 load generation → F05 validate unique ID, org/SLH, controller, domain, network → F06 propose successor → F07 verified commit | M02 → M03 → M04 | Accepted SEH target reservation with new verified state binding | P |
| `SEH-O03 plan` | F01 bind current generation → F05 resolve desired target → F08 render pinned Compose + canonical digest → F09 freeze approval inputs | M02 → M03 → M06 → M07 | Reproducible **dry-run** plan; zero Coolify writes | E→P |
| `SEH-O04 preflight` | F10 resolve controller credentials in memory → F11 verify Coolify coordinates and collisions → F12 verify DNS, TLS, image/release, org public-key binding → F13 revalidate frozen plan | M02 → M05 → M06 → M07 | Refusal or explicit ready-to-mutate checklist, no remote mutation | E→P |
| `SEH-O05 deploy` | F09 validate exact target/plan/authority → F14 create or reconcile exact resource → F15 await the exact deployment ID → F16 runtime observation → F17 verify route → F07 commit accepted result | M07 → M08 → M09 → M10 → M11 → M04 | Correct Coolify service, complete deployment, authenticated relay; generation update only after proofs | E→P |
| `SEH-O06 verify` | F16 verify runtime health → F17 verify TLS/WSS + authenticated SLH tunnel → F18 execute remote Known Device and test paid path | M10 → M11 → M12 | Separate gate verdicts: container, public transport, SLH identity, remote work, ledger | E→P |
| `SEH-O07 status` | F01 read accepted target → F03 observe live endpoints/receipts → F04 classify drift, degradation and last test | M02 → M05 → M10 | No write; explicit accepted-versus-observed difference | P |
| `SEH-O08 reconcile` | F04 detect drift → F13 compare expected generation and source digest → F19 select resume/repair vs no-op → F14 approved mutation only if requested → F16/F17 reverify | M05 → M07 → M13 → M08 → M10 → M11 | Converged matching version or bounded unresolved drift; no silent adoption | P |
| `SEH-O09 upgrade` | F08 freeze new pinned release → F09 authorize exact change → F20 drain/protect in-flight tunnels → F14 update deployment → F15 await terminal result → F17/18 reaccept | M06 → M07 → M14 → M08 → M09 → M11 → M12 | Release proof + reconnection; no accepted upgrade on unverified new runtime | P |
| `SEH-O10 disable/enable` | F05 validate target/policy → F09 authorize → F21 write SLH route policy → F07 commit desired state → F17 observe result | M03 → M07 → M15 → M04 → M11 | Disabled: no new SLH connections; enabled: verified route eligible for autoconnection | P |
| `SEH-O11 retire` | F09 authorize selected resource → F20 drain → F22 delete/retain exact SEH resource → F23 verify removed/nonreachable → F07 commit tombstone | M07 → M14 → M08 → M10 → M04 | Exactly selected SEH retired; no unrelated Coolify resources deleted | P |
| `SEH-O12 recover` | F24 load interrupted operation and receipts → F01 bind current generation → F03 observe actual Coolify state → F19 resume/compensate only authorized step → F16/F17 reverify | M13 → M02 → M05 → M08 → M10 → M11 | Reconciled receipts/state without duplicate creates or unsupported rollback | P |

### 3.1 Functionality catalog

| ID | Contract and side-effect boundary |
|---|---|
| F01 | Read only canonical, generation-bound shared private-state document; refuse stale or invalid binding. |
| F02 | Enumerate SEH logical targets and public metadata; never expose controller credentials. |
| F03 | Read-only Coolify service inventory, deployment details, DNS, health and tunnel observation. |
| F04 | Compare desired/accepted/observed identities, release versions, and health without auto-adoption. |
| F05 | Validate ID uniqueness, controller references, organization/SLH identity, public URL, resource ownership and policy. |
| F06 | Prepare additive, canonical private-state successor preserving other networks and records. |
| F07 | Commit a verified successor only after matching generation/CAS and required proof; leave no partial accepted state. |
| F08 | Build deterministic image-digest-pinned Compose and exact desired-state hash. |
| F09 | Bind explicit mutation authorization to exact target, network, controller, plan hash, current generation and action. |
| F10 | Resolve Coolify credential only inside the secret-safe adapter; reject missing/overprivileged target configuration. |
| F11 | Read-only Coolify project/environment/server and service UUID/ownership checks. |
| F12 | Verify release provenance, public domain/TLS/WSS prerequisites, bound SLH public signing key and exposure policy. |
| F13 | Refuse changed generation, image, resource UUID, controller or approval scope between preflight and execution. |
| F14 | Create/update/start selected Coolify service, no duplicate POST on ambiguous response; retain exact request/deployment receipt. |
| F15 | Watch asynchronous Coolify deployment to a terminal outcome; accepted HTTP request is **not** success. |
| F16 | Observe SEH process identity, `/healthz`, resource version, and readiness; diagnose failures. |
| F17 | Verify externally published HTTPS/WSS, SLH identity challenge, tunnel lifecycle, E2E TLS fingerprint and relay rejection gates. |
| F18 | Route a Known Device's authorized paid smoke request through **the selected remote SEH**, prove actual worker execution, one requester charge, one wallet earning, and local lab ledger settlement. |
| F19 | Use frozen operation ID, accepted generation and receipt to select no-op, resume, explicit repair or refusal. |
| F20 | Drain session admission; protect accepted in-flight work; define bounded reconnect and rollback/forward-recovery semantics. |
| F21 | Write enabled/disabled SLH route settings via the SLH-owned configuration adapter, not the Coolify mutation adapter. |
| F22 | Delete only the exact approved SEH resource after verifying ownership, without deleting another organization's service. |
| F23 | Verify final remote resource state; record tombstone and audit evidence, never treat a queued delete as removal. |
| F24 | Recover interrupted runs from immutable recorded approvals, deployment receipts and fresh observations. |

## 4. Module catalog and ownership

The **M IDs are design identifiers**, not existing imports. `Existing` identifies source code verified in this snapshot; `Target` names a proposed refactoring location. The eventual package names can change if the call contracts remain testable.

| ID | Module, current source or proposed target | Authority class | Responsibility |
|---|---|---|---|
| M01 | `tools/seh_harness.py` **existing**, to become a thin entry point | orchestrator | CLI parsing, stage/operation selection, summary, call into common backend; never hold Coolify business logic or secret material |
| M02 | `tools/seh_control/common/privates.py` **target**; patterns in `tools/hub_control/common/privates.py`, `tools/fdb_control/common/privates.py` | reader | Load verified shared infrastructure identity, resolve approved controller coordinates and ephemeral credential references; independent from Mother operations |
| M03 | `tools/seh_control/common/models.py` **target**; existing `main_computer/slh/config.py:SEHTarget` | pure | Typed SEH identity, placement, registration, URL validation, desired vs observed schemas |
| M04 | `tools/seh_control/common/state.py` **target**; verified successor methods in `tools/mother/common/private_state.py` are reference contracts | authority-writer | Generation-bound SEH registry successor, conflict detection, accepted-state/tombstone commit; no direct YAML overwrite |
| M05 | `tools/seh_control/inspect.py` **target**; current `main_computer/slh/coolify.py:SEHDeployer.preflight` partly overlaps | reader | Cross-check Coolify inventory, ownership marker, accepted registry and service health; no mutation |
| M06 | `tools/seh_control/common/desired.py` **target**; current `main_computer/slh/coolify.py:compose_yaml`, `desired_state`, `plan` | pure | Immutable image/release, Compose, policy, plan hash; never store signing private keys |
| M07 | `tools/seh_control/common/approvals.py` **target**; guarded exact approvals currently in `tools/seh_harness.py` and `SEHDeployer` | ledger-writer | Frozen plans, mutation authority, run ID, state preconditions, approval scope and audit |
| M08 | `tools/seh_control/common/deployment.py` **target**; current `CoolifyHTTP`, `SEHDeployer.deploy/start_existing`; local adapter in `tools/slh_seh_lab/local_coolify.py` | live-adapter | Coolify HTTP and service create/update/start/delete limited to approved target and action |
| M09 | `tools/seh_control/common/deployment_watch.py` **target**; Hub/FDB Coolify deployment watchers as reference | reader | Track exact queued deployment UUID to terminal outcome; retain bounded error evidence |
| M10 | `tools/seh_control/common/observations.py` **target** | reader | `/healthz`, `/readyz`, endpoint, resource identity, deployment reconciliation and teardown observations |
| M11 | `tools/seh_control/common/relay_verify.py` **target**; `main_computer/seh/relay.py`, `main_computer/slh/seh_transport.py` are runtime implementations, not management writers | reader | Validate authenticated tunnel, public WSS/E2E, forbidden identity claims, recovery and disconnection |
| M12 | `tools/seh_control/common/acceptance.py` **target**; existing `tools/slh_seh_lab/run_lab.py` and `paid_cycle.py` | orchestrator | Reuse unified lab for paid and isolated external acceptance; never create a stand-in relay for a remote-deployed verdict |
| M13 | `tools/seh_control/common/recovery.py` **target** | orchestrator | Resume by exact operation/deployment receipt and accepted generation, handle ambiguous responses safely |
| M14 | `tools/seh_control/common/drain.py` **target** | orchestrator | Coordinate transition of public sessions, bounded reconnect and admission hold-off during upgrade/retirement |
| M15 | `main_computer/slh/seh_autoconnect.py` **existing**, optional small SLH-owned management adapter **target** | local-authority-writer | Persist `seh-connection.json` enabled route and supervise authenticated outbound connection; private SLH signer remains local |

**Dependency rule:** `CLI / SLH API / future UI → SEH Control orchestration → (shared identity reader, state writer, Coolify adapter, relay/acceptance readers)`. Runtime `SEHRelay` must not import SEH deployment control; the SLH gateway must not become a Coolify credentials store; the new control library must not import Mother operation lifecycle implementation.

## 5. Operator workflows and interfaces

### 5.1 Already runnable (current repository)

```powershell
# Read-only older SEH plan/preflight over an explicit, validated target JSON.
python -m tools.seh_harness plan --target <validated-seh-target.json>
python -m tools.seh_harness preflight --target <validated-seh-target.json>

# The existing automated local Coolify deployment and actual deployed paid lab.
python -m tools.slh_seh_lab.run_lab deploy-local-coolify --reuse-image --org-id my-company --slh-id my-slh
python -m tools.slh_seh_lab.run_lab verify-deployed
```

`preflight` against a remote target uses the existing `SLH_COOLIFY_TOKEN` path today; the design in this document replaces that standalone selection with identity-backed resolution. **The shown target path is a placeholder.** The local lab can use a running Coolify service and synthetic smoke credits; it does not by itself verify external WSS or bridge payout.

### 5.2 Intended future harness experience (not executable yet)

```text
seh_harness list --network mainnet
seh_harness register --network mainnet --seh company-primary ...
seh_harness plan --network mainnet --seh company-primary
seh_harness preflight --network mainnet --seh company-primary
seh_harness deploy --network mainnet --seh company-primary --execute-mutations --approve-plan <digest>
seh_harness verify --network mainnet --seh company-primary --through paid-cycle
seh_harness status --network mainnet --seh company-primary
seh_harness upgrade / disable / enable / retire / recover ...
```

These are **human-level command examples**. Final spelling, mutation confirmation flags and admin authorization will be fixed in implementation after comparing Hub/FDB/Mother harness conventions. The normal operator provides **logical SEH identity**, not Coolify bearer tokens, ad hoc Compose YAML, manually copied service UUIDs, or manually started connectors. The harness resolves those beneath the frozen plan and states exact approval requirements before mutation.

### 5.3 SLH service and future UI integration

The SLH service consumes the verified SEH record and persists a public-only enabled connection configuration. On startup it maintains the tunnel with its own signing identity. A remote SEH deployment must **not** cause the service to trust arbitrary workers or turn on marketplace spending. The future administration UI should present the same phases: target, controller/domain, plan and approval, deployment, remote acceptance, enabled/disabled state, last verification, and explicit errors. UI permissions must require a currently authenticated administrator with the proper deployment authority, independently of ownership of the client device.

## 6. Acceptance gates, evidence and refusal rules

| Gate | Required evidence | Must **not** be mistaken for |
|---|---|---|
| G01 identity | Valid shared generation and one unique target/controller/org binding | A YAML entry copied by hand |
| G02 plan | Exact canonical desired-state digest with source generation and pinned image | A generic deployment template |
| G03 preflight | Actual controller authorization and project/environment/server/service identity plus release/DNS/TLS checks | Network reachability alone |
| G04 mutation | Explicit approved plan and exact target; specific Coolify resource/deployment receipt | HTTP 200/202 or service UUID as completed deployment |
| G05 runtime | Terminal deployment success, correct SEH identity and reachable `/healthz` | Correct SLH tunnel or remote job execution |
| G06 remote transport | Public WSS with validated certificate; Known Device route preserves SLH TLS fingerprint; incorrect identity/pin rejected | Local `ws://127.0.0.1` |
| G07 service | Authenticated configured SLH connection, reconnect on dropped tunnel, stops on shutdown/disable | Isolated disposable lab tunnel alone |
| G08 economics | Paid job traversed **selected** SEH to a real managed worker; one debit and one wallet earning; settlement classified as smoke/local vs external | Worker advertised but no job executed, or external blockchain payout |
| G09 lifecycle | Restart/upgrade/retire proofs and no leaked/duplicated sessions or credentials | A queued Coolify change |
| G10 private-state | Generation advance only after required proofs; exact CAS, recovery closure, no unrelated records altered | Best-effort overwrite or report-only PASS |

Refuse mutation before any remote write if: identity binding changed; controller/target cannot be resolved uniquely; image digest unpinned; domain not approved; public key mismatches organization; service UUID/marker collides; deployment operation or permission is not explicitly approved; or a previously submitted POST has an ambiguous outcome requiring observation. For update/retire, require exact selected resource and a safe drain decision. Keep local lab and production credentials isolated.

Every operation writes a bounded redacted report containing: schema/version, network/SEH ID, source generation and hash, plan digest, operation ID, target Coolify service and deployment UUIDs (if known), per-gate verdicts, observed vs accepted state, retry/recovery classification, and timestamps. Reports never include Coolify bearer tokens, private signing keys, worker keys, wallets, full protected payloads, or plaintext work.

## 7. Delivery sequence and traceability

| Slice | Implementation/output | Minimum proof |
|---|---|---|
| D1 — identity reader and target registry | M02–M04 with registered SEHs under `networks.<network>`; shared file validation and generation-safe update; no remote mutation | Unknown-field compatibility, controller resolution, secret redaction, wrong network, stale generation, concurrent successor conflict |
| D2 — remote harness and management API | M01, M05–M10, M13, M14; one reusable backend behind CLI and SLH service | Read-only plan, exact approval, deploy, adoption/refusal, rerun idempotency, asynchronous deployment, failure recovery, upgrade/retire gates |
| D3 — remote acceptance | M11–M12 and SLH connection adapter M15; extend existing SLH/SEH lab | Public TLS/WSS and authenticated tunnel, incorrect identity rejection, paid job through exact remote SEH, disconnect/reconnect, verified claim boundaries |
| D4 — UI (later) | SLH admin views call D2 backend and display D3 proof | Role authorization, parity with CLI, no additional Coolify or signing-key implementation |

**Do not declare D1 implemented merely because this document specifies the record shape.** Current remote SEH target JSON, remote credential environment variable, and early Coolify create/start interfaces remain until the corresponding migration is executed and tested. A simple document patch does not grant new deployment authority.

## 8. Design decision summary

The canonical source is **shared infrastructure identity**, not a second SEH credentials silo. The SEH lifecycle is **separate from Mother chain, FDB membership, and Hub economy**. The SLH owns device trust, signing keys, and its persistent SEH connection. Coolify owns deployment execution. SEH Control owns the authorized operation, frozen plan, verified receipt and accepted SEH management state. The unified SLH/SEH lab supplies **execution evidence**, first locally and then against the selected public deployment.

**In one sentence:** an administrator should be able to name an SEH, approve a reviewed plan, and let the same backend safely deploy, verify, supervise, upgrade, and retire that SEH—with no repeated manual tokens, script juggling, or identity-copying into containers.

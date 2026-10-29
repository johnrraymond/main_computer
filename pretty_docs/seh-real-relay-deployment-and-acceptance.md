# Secure External Hub — Real Relay Backend, Coolify Deployment, and Integrated Lab

## Status and scope

This phase implements an **actual SEH WebSocket relay service**, an outbound SLH-to-SEH tunnel, and an SEH client adapter for the existing walletless Managed Worker. It does not depend on the future SLH administration UI. The SLH keeps its wallet keys, device registry, and pinned TLS private key. The SEH knows only a **public** SLH P-256 relay identity and opaque traffic frames.

**Acceptance evidence:** `python -m tools.slh_seh_lab.run_lab run` tests the real SEH runtime locally against the existing three-Hub smoke topology. It exercises a managed worker through the real relay, proves the paid job reached that worker, checks requester charge, wallet-owned earning, idempotency and local ledger settlement. The `verify-local` alternative uses a real in-memory Stable Hub. These tests **do not deploy to an external Coolify server or prove public HTTPS/WSS, real-world packet filtering, host restarts or blockchain bridge transfers**.

## 1. Trust boundaries

The local **SLH** is the organization's authority. It validates enrolled devices using Main Computer Authenticator keys and gives those devices access according to SLH policy. The **SEH** is an optional rendezvous service: it validates the SLH's service signing proof, then forwards opaque data between each public client and the SLH's private TLS listener. The **Stable Hub** remains the marketplace payment and worker-session authority. The **Coolify API key** authorizes deployment operations only.

There are no organizational certificate authorities or per-worker certificate enrollment procedures. The SLH's already-existing self-signed TLS leaf is pinned by each managed Worker. The SEH cannot decrypt or forge this TLS session. The public SEH URL must be **WSS over normal public HTTPS**, with TLS issued by the configured Coolify proxy; plaintext WebSocket is restricted in code to explicitly enabled loopback lab connections.

The SEH receives an SLH **public** P-256 key in its image configuration. It challenges a connecting SLH with a fresh random nonce; the SLH signs the challenge using its separate persistent SEH service key. The SEH admits only one authenticated outbound SLH tunnel per organization/instance. If it disappears, connected remote clients fail closed and must reconnect. The SEH does not approve devices, issue marketplace MSKs, authorize administrator actions, or store prompts/results.

## 2. Build the SEH container

From the repository root, where Docker is installed:

```powershell
# The Dockerfile copies only the small SEH runtime and common signature verifier.
docker build -f deploy/seh/Dockerfile -t registry.example.com/main-computer/seh:phase3 .
# Push using your organization's registry login.
docker push registry.example.com/main-computer/seh:phase3
# Record the registry-reported digest for the pushed image.
docker buildx imagetools inspect registry.example.com/main-computer/seh:phase3
```

Copy the real image repository and immutable `sha256:` digest into a new copy of `deploy/seh/target.example.json`. **The example image and all-zero digest are placeholders and are intentionally refused by mutation commands.** The Coolify Compose generator references `<image>@<sha256-digest>`, uses a read-only filesystem, drops Linux capabilities, and publishes no direct host port. Coolify's proxy must target the SEH internal port, 8080 by default.

## 3. Provision the SLH relay identity

Run this **on the organization's SLH machine**, not the SEH server:

```powershell
python -m tools.slh_seh_connector identity
```

This creates `seh-service-signing-key.pem` in the local SLH state directory if missing and prints the **public_key_b64** value. The private key remains only on the SLH host. Put the public value in the `slh_public_key_b64` field of the Coolify target JSON. Do not copy the private PEM to a Coolify variable, image, remote worker, USB installer or report.

The service identity here is distinct from the organization's marketplace wallet and from each worker's personal Known Device authenticator. Replacing this identity requires an explicit SEH configuration change; it does not change worker enrollment.

## 4. Coolify API deployment

The existing `tools.seh_harness` commands remain the single guarded deployment interface. Supply the Coolify API token to the **SLH host process** as `SLH_COOLIFY_TOKEN`; it must not be included in the target JSON or Compose:

```powershell
python -m tools.seh_harness plan --target .\my-seh-target.json
python -m tools.seh_harness preflight --target .\my-seh-target.json
```

Review the plan's target ID and `plan_digest`; deployment creation requires `--execute`, the exact target ID, and exact digest:

```powershell
python -m tools.seh_harness deploy --target .\my-seh-target.json `
  --execute --approve-target <target_id> --approve-digest <plan_digest>
```

Record the returned `service_uuid` in the target. Run `plan` again because the target and digest changed, then start the explicitly selected existing resource:

```powershell
python -m tools.seh_harness start --target .\my-seh-target.json `
  --execute --approve-uuid <service_uuid> --approve-digest <new_plan_digest>
```

The created service is **not marked ready** by these API calls. In Coolify, assign the target HTTPS hostname to the `seh` service and direct it to the configured internal port. Ensure public DNS resolves to the selected Coolify host and that its proxy obtains a trusted HTTPS certificate. Verify that reverse-proxy WebSocket upgrades work. The first revision leaves this domain-to-component mapping as a separately verified Coolify operation; it **does not guess an undocumented `urls` API payload**. Coolify's create-service endpoint accepts the base64-encoded Compose service, and service start is a separate operation. Start success alone does not prove DNS, TLS, device authentication or relay readiness.

The SEH's `GET /healthz` reports process health; `GET /readyz` reports whether a currently authenticated SLH tunnel is connected. Neither proves private-work confidentiality on its own.

## 5. Start the SLH outbound connector

The existing managed worker gateway must already be running and listening on its canonical local port `8898`:

```powershell
python -m tools.slh_managed_gateway serve
```

In another SLH process/terminal, run:

```powershell
python -m tools.slh_seh_connector run `
  --seh-url wss://hub.secure.company.com `
  --org-id my-company --slh-id my-slh
```

Use the organization/SLH IDs configured in the Coolify target. This command reads the local SEH service signing key, connects outbound through HTTPS/WSS, signs the SEH's challenge, and reconnects on disconnect with a bounded delay. The SEH cannot open an arbitrary outbound connection into your private network; the SLH connector only talks to its own local managed gateway on `127.0.0.1`.

An external worker connects to the SEH using the provided remote adapter. A future Worker application UI can launch the same adapter automatically. The adapter listens on an ephemeral **loopback** port, forwards opaque bytes through the remote WSS relay, and connects the unchanged existing Managed Worker TLS client through it. The Worker still checks the **SLH-managed TLS pin** and performs its Known Device challenge with the actual SLH, not with the SEH.

## 6. One-command integrated acceptance

```powershell
python -m tools.slh_seh_lab.run_lab verify-local
python -m tools.slh_seh_lab.run_lab run
```

The `run` lane uses the existing `deploy/hub-topology/smoke-topology.json` and disposes of only its own Hub processes. Both modes start the **real** SEH relay service and authenticated SLH tunnel on loopback. The harness forces the paid public request to the SEH-connected managed worker, rather than allowing the Stable Hub to select a direct LAN worker. It then restores public allocation and drains the sessions. The report distinguishes real local SEH runtime acceptance from **live Coolify/public WSS NOT TESTED**, and synthetic smoke accounting from bridge-funded settlement.

Security gates include invalid SLH signing key, duplicate SLH admission, reconnect after disconnect, Known Device authentication through the relay, wrong SLH TLS pin, isolated wallet authority, and no duplicate credit charge. The SEH has no access to the plaintext private workload; this property depends on the existing SLH pinned TLS handshake remaining end-to-end. The application and user-specific SLH administration API over SEH remains a later integration target: this phase routes the managed-worker gateway.

## 7. Implementation boundaries and future work

The SEH runtime is now code rather than a simulated relay. **Do not claim a live Coolify deployment until you actually build/push the pinned container, deploy it with an API token, configure a public HTTPS domain, and run a public WSS acceptance test.** The image build itself was not performed in the ChatGPT runtime without Docker/registry access. Neither the Android phone app, remote administrative SLH UI, private B2SelfB write-down, nor external token bridge payout is completed by this change.

## Local Coolify deployment without API access (lab setup)

For the Coolify server already running in the *same Docker engine* as the Main Computer checkout, there is no need to publish an image or create a remote API key. Coolify supports deploying a **Docker Compose Empty** service through its administrator dashboard. This is not an unattended/API deployment: the operator must still save and deploy the service in Coolify.

Use the existing lab CLI to build the SEH image and create a local Compose definition from the SLH's **public** relay identity:

```powershell
python -m tools.slh_seh_lab.run_lab prepare-local-coolify --build-image `
  --org-id my-company --slh-id my-slh
```

The command checks that `docker ps` can see the running Coolify instance before building, preventing an accidental build into the wrong Windows/WSL Docker context. It writes `runtime/reports/slh-seh-lab/local-coolify/compose.yml`. No Coolify API call, token, remote image registry, private worker key or wallet is used. The image is tagged `main-computer-seh:local` and the generated Compose uses `pull_policy: never`, an isolated container and a **loopback-only** test port `127.0.0.1:18080`.

In the local Coolify dashboard, open an existing project/environment, select **New Resource → Docker Compose Empty**, paste the generated `compose.yml`, select the *localhost* server using the **same Docker engine**, save, and click Deploy. If Coolify cannot see the image, inspect its actual selected server and daemon before proceeding; never change `pull_policy` merely to hide this error.

After deployment, verify process health:

```powershell
Invoke-RestMethod http://127.0.0.1:18080/healthz
```

`/readyz` returns unavailable until the SLH establishes an outbound authenticated relay tunnel. Start the SLH managed gateway on port 8898 and, in another terminal, its local-lab connector:

```powershell
python -m tools.slh_managed_gateway serve
# In a separate PowerShell window:
python -m tools.slh_seh_connector run --org-id my-company --slh-id my-slh `
  --seh-url ws://127.0.0.1:18080 --allow-loopback-lab
Invoke-RestMethod http://127.0.0.1:18080/readyz
```

The opt-in `--allow-loopback-lab` flag permits unencrypted WebSocket **only** to localhost/127.0.0.1; it does not authorize plaintext connections to any remote endpoint. Loopback HTTP/WS is for **local lab verification only**. Production remote connections still require Coolify-managed HTTPS/WSS, domain routing, and external verification. Do not count the image build or Compose preparation as an actual Coolify deployment, or process health as completed SLH/SEH integration.

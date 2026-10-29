# SEH — Automatic Local Coolify Deployment (no operator-supplied API key)

## One-command deployment

From the Main Computer repository on the Windows machine with the existing local Coolify instance:

```powershell
python -m tools.slh_seh_lab.run_lab deploy-local-coolify --org-id my-company --slh-id my-slh
```

This command automatically builds `main-computer-seh:local` in the Docker engine containing local Coolify; reads the SLH's previously created **public-only** SEH relay identity; renders its SEH Compose; reconciles a named Coolify service under the local smoke project; requests a Coolify deployment; drains Coolify's local deploy queue; and verifies a SEH-specific `GET /healthz` on `http://127.0.0.1:18080`.

Unlike `prepare-local-coolify`, the new command **does deploy**, with no manual dashboard, pasted Compose, registry push, or operator-supplied token. The existing `main_computer.publishing.local_server_prepare._load_coolify_local_docker()` adapter used by Website Builder loads `tools/local-prod/coolify-local-docker.py`. The same local operator performs the self-SSH/Docker target preflight, auto-acquires the local API token, finds/creates the project and environment, creates/updates the Compose service, triggers `/v1/deploy`, and drains the queue. The local Coolify API token stays in its existing on-machine state; it is not printed, passed to the SEH container or written to the lab report. A local API is used internally; **no remote Coolify key or target is needed**.

Re-running the command reconciles the same `main-computer-seh-<organization>-<slh>` resource rather than creating a new arbitrary application. To reuse an already-built image without rebuilding:

```powershell
python -m tools.slh_seh_lab.run_lab deploy-local-coolify --reuse-image --org-id my-company --slh-id my-slh
```

To retain the former preparation-only operation:

```powershell
python -m tools.slh_seh_lab.run_lab prepare-local-coolify --build-image
```

## Acceptance and scope

The deploy command returns `DEPLOYED_VERIFIED` **only when** the local Coolify resource has been reconciled, the deployment request accepted, its queue drained without reporting failure, and the loopback HTTP health response identifies `main_computer.seh` and reports `live: true`. It refuses non-loopback Coolify control endpoints. On failures it returns nonzero and does not claim deployment.

`/healthz` proves that the SEH process is responding, not that the SLH is connected. The next operational check is an authenticated SLH outbound tunnel (the existing `tools.slh_seh_connector run` flow), then `http://127.0.0.1:18080/readyz`. **The existing `run` and `verify-local` tests still use disposable in-process SEH instances; they do not silently claim that their paid-worker cycles traversed this Coolify-deployed instance.** Remote public WSS and external blockchain settlement remain separate acceptance targets.

Local Coolify may use a mutable API token automatically generated and stored by the existing Website Builder local provisioning code. That is different from asking the operator to create or paste a remote token. This deployment modifies only the SEH service under the local smoke Coolify project; it does not deploy to production, expose a public port, or alter marketplace wallets.

## SLH startup and persistent SEH connection

Successful `deploy-local-coolify` now saves an enabled `seh-connection.json`
inside the existing SLH state directory, with the verified local SEH URL and
organization/SLH identities. It contains no keys or API tokens. On the **next
SLH managed-worker gateway startup**, the gateway automatically connects,
authenticates with its existing service-signing key, and reconnects on tunnel
loss. A standalone `tools.slh_seh_connector run` process is not necessary and
should be stopped before using this managed mode (two primary connections
would compete for the SEH's single primary slot). No external connection is
opened when the route is absent or disabled. Use the integrated lab to test
startup, recovery, and clean shutdown; `verify-deployed` uses an isolated
lab identity/channel and never replaces the organization's primary tunnel.

# All-father control plane bootstrap

The all-father deployment starts with a control surface, not with a guessed
mainnet or testnet workload topology.

`runtime/state/all_father.private.yaml` is only the seed/control file. It should
tell the tooling which Coolify hosts participate and how to talk to those
Coolify APIs. It is not a declaration that any Hub, FoundationDB, QBFT,
`hub_admin`, or contract service exists.

## First operation

Push one all-father guard/head container to every Coolify host in the high-level
topology:

```powershell
python tools/allfather_control.py bootstrap-heads --dry-run
```

Then apply:

```powershell
python tools/allfather_control.py bootstrap-heads
```

The head container exposes the remote peer guard API in the 41400 range and
carries only the peer head list. Those `10.x` guard URLs are for head-to-head
traffic inside the remote VPN/control network. They are not assumed to be
reachable from the local operator machine. The head does not start Hub, FDB,
QBFT, hub_admin, or contracts.

The normal bootstrap command does not ask the operator to choose a Coolify
project name. When Coolify requires a project lookup, the control plane uses the
default Coolify project name `My first project`.

## Bootstrap image

The control-plane head is intentionally self-contained. It uses the public
`python:3.12-slim` image and an inline stdlib HTTP guard server in the generated
Compose. Coolify does not need a repository build context and does not pull a
private `main-computer-allfather-head:latest` image for the first bootstrap.

## Discovery before topology

After the heads are deployed, query the live control surface through Coolify:

```powershell
python tools/allfather_control.py discover
```

Discovery does not use SSH, does not curl the remote `10.x` VPN guard URLs from
the local operator machine, and does not create public guard DNS or Traefik
routes. Instead it uses the Coolify API/key path to create or update one
long-running private probe service per Coolify host:

```text
local operator -> Coolify API -> Coolify-managed probe service -> private guard URLs
```

The probe service has no FQDN and no host port. It runs inside the remote
Coolify-managed Docker context, curls the private guard URLs from there, and
writes `ALLFATHER_PROBE_RESULT ...` JSON lines to its container logs plus
`/state/latest-result.json` for remote diagnosis. The probe intentionally stays
running after discovery so we can inspect it in Coolify when something acts up.
A future finalization action can stop or remove probes after the control path is
stable.


The result reader uses Coolify's application-log API (`/api/v1/applications/<application_uuid>/logs`)
when the probe service detail includes an embedded application UUID. Parent
service log paths are kept only as compatibility fallbacks because service-level
`/api/v1/services/<uuid>/logs` paths may return `404` on current Coolify
versions.

Preview the probe payloads without changing Coolify:

```powershell
python tools/allfather_control.py discover --dry-run
```

Mainnet/testnet topology must come from live guard responses and explicit
add/remove operations, not from the private seed file. `discover` reports whether
the Coolify-managed probes were synced and whether any probe result was observed;
it does not treat the private seed file as topology.


## Output verbosity

`discover` uses an operator summary by default. The normal output includes only
the readiness reason, high-level counts, head/probe service IDs, whether a probe
result was observed, and a single compact last error. It does not dump Coolify
API attempt lists, embedded Compose text, server settings, token sources, probe
targets, or failed log-endpoint paths.

Use `--json` when a compact machine-readable diagnostic payload is needed:

```powershell
python tools/allfather_control.py discover --json
```

Use `--verbose` only when a raw diagnostic dump is needed:

```powershell
python tools/allfather_control.py discover --verbose
```

Verbose discovery may print large Coolify API payloads and should not be pasted
into normal chat/debug loops unless those raw records are needed.

## Guardrail

`hub_admin` and contract setup are blocked until discovery shows at least one
live QBFT validator-RPC node. A Hub super-node can be pushed before public cutover,
but public Hub cutover also waits for a live QBFT validator-RPC node.

## Write local artifacts

For inspection only:

```powershell
python tools/allfather_control.py write-heads `
  --out runtime/coolify-allfather/heads
```

This writes one guard/head manifest and one compose file per Coolify host.


## Compact discovery output

`discover` should return `ok=false` until at least one private probe result is
observed from the running probe services. Syncing the probe services is not
enough to call topology discovery healthy.

Normal operator summary:

```powershell
python tools/allfather_control.py discover
```

Detailed compact JSON:

```powershell
python tools/allfather_control.py discover --json
```

Raw diagnostics:

```powershell
python tools/allfather_control.py discover --verbose
```

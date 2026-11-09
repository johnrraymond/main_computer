# Dev-Test Hub — P3 identity and network boundary

The Dev-Test Hub is the **local** development/test implementation; it is not a
fourth production Hub role. Stable Hub remains the separate public testnet and
mainnet runtime. Secure Local Hub (SLH) and Secure External Hub (SEH) retain
independent lifecycles.

## Startup

`start.bat` launches the Dev-Test Hub by default. `--no-dev-hub` suppresses
only that Hub. `--no-slh-hub` independently suppresses SLH startup (neither
flag registers an SLH Windows-boot task). `dev-hub-start.bat` remains a
standalone compatibility launcher. Existing PID and runtime file locations
remain unchanged.

The default Dev-Test implementation is the experimental FoundationDB topology
(`exp-fdb-hub.py`, development ports 8871–8873). The compatibility mode is the
older broker, launched through `python -m main_computer.cli dev-test-hub`.
Its old `hub` CLI spelling remains available only with explicit local `--network
dev` or `--network test`; it will refuse a public network, including when the
registry defaults to mainnet.

## Network boundary

The Dev-Test Hub accepts only explicit `dev` (chain 42424242) or local `test`
(chain 42424241) profiles. The latter is distinct from **public testnet** even
though their chain IDs match. The startup preflight additionally requires:

- A loopback HTTP Hub listener and advertised URL, with explicit ports.
- A loopback HTTP chain RPC URL.
- A consistent local network, chain ID, entry-URL list, and topology record.
- Agreement between the explicitly requested network and the selected topology.

The preflight is read-only and runs before the Dev-Test Hub startup helper
stops/replaces its managed listener. It must refuse mainnet, public testnet,
public RPC addresses, and mixed-topology definitions. An intentionally invalid
Dev-Test configuration must not be 'fixed' by falling back to the registry's
mainnet default.

The Viewport's old `HubProvider` and the older remote-overflow route are
Dev-Test-only. They may use loopback or documented development-container
aliases (`hub`, `dev-hub`, `dev-test-hub`, `host.docker.internal`), but cannot
send work to public Stable Hub endpoints. Their loopback retry is limited to
those documented aliases, not arbitrary remote DNS failures. The local Hub URL
default is 127.0.0.1:8871, matching the standard Dev-Test topology.

## No production mutation

This phase changes local startup, CLI/provider selection, local UI defaults,
and reporting. It does **not** redeploy Stable Hubs or SEH, change SLH policy,
modify Mother, change public network registries, or remove older endpoints.
Further historical code retirement is reserved for P5.

## Read-only preflight and tests

```powershell
python -m main_computer.dev_test_hub preflight `
  --network dev --network-kind dev --requested-network dev `
  --chain-id 42424242 --chain-rpc-url http://127.0.0.1:18545 `
  --host 127.0.0.1 `
  --hub-url http://127.0.0.1:8871 `
  --hub-url http://127.0.0.1:8872 `
  --hub-url http://127.0.0.1:8873 `
  --topology deploy/hub-topology/dev-topology.json

python -m pytest -q tests/test_dev_test_hub_p3_isolation.py
```

On Windows, also verify `start.bat`, `start.bat --no-dev-hub`, and
`start.bat --no-slh-hub` against the live local service inventory; never
conflate process launch with the reported service readiness state.

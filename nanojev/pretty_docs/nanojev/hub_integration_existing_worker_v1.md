# NanoJev/CLEF through the existing Stable Hub

This integration **does not introduce a new Hub server, Hub image, or wire protocol**.
It uses the same `data engage computer --agent` worker, the same wallet-backed
multi-session authorization, and the same `hub.work.offer` / `worker.work.result`
WebSocket messages as Ollama.

## Worker

Start the managed NanoJev lifecycle manager normally. The CLEF backend can
remain stopped until the first inference: the provider points at the manager
port `127.0.0.1:9765`, not the transient backend port 9766.

```powershell
python -m main_computer.cli data `
  --provider nanojev `
  --model nanojev-clef `
  --nanojev-base-url http://127.0.0.1:9765 `
  --nanojev-timeout-s 1200 `
  --hub-worker-node-id data-nanojev-clef `
  engage computer --agent `
  --hub-url https://mainnetc-hub1.greatlibrary.io
```

The standard WebSocket worker now advertises two *native* capabilities,
`nanojev.clef.evaluate.v1` and `nanojev.clef.pairwise-batch.v1`. It does NOT
advertise `chat.completions`. Ollama's existing worker retains its chat offer
and execution path.

The worker rejects a request unless the `work.capabilities` array names
**exactly one** supported CLEF capability, `work.input.kind` is identical to
that capability, and `work.input.payload` is a nonempty JSON object. It then
passes the request unmodified to the already-tested NanoJev local provider.

## Request

Prepare `clef_request.json` in the existing native CLEF evaluate format, then
preview the exact Stable Hub request **without network traffic or spending**:

```powershell
python -m tools.nanojev_hub_request `
  --payload-file .\clef_request.json
```

To run a paid test through the existing Hub and retrieve its completed native
solution, explicitly opt into submission:

```powershell
python -m tools.nanojev_hub_request `
  --hub-url https://mainnetc-hub1.greatlibrary.io `
  --payload-file .\clef_request.json `
  --timeout-s 1200 `
  --max-price 1 `
  --submit
```

The submitter signs using the existing O3 officer wallet, requires a Stable Hub
identity, and uses the current `/api/hub/v1/work/requests` endpoint. It obtains
`continuation_url` and reads the full `accepted_session.worker_result.result.solution`
from the established session stream endpoint. The stream's chat-oriented `content`
may be empty because a CLEF solution is *not* a chat completion. The accepted
session stores the full typed solution, checkpoint identity, and payout receipt.

`--max-price` bounds the authorized price in compute credits. Failed jobs must
not be described as successful solves; whether the Hub releases a hold is
verified by the accepted session's payout record, not inferred from model output.

## Verification gates

1. Local provider tests and health/inference smoke (already proven on checkpoint 136).
2. Current worker advertises native capabilities, existing worker socket accepts it.
3. Hub request matches CLEF market capabilities and is accepted under the signed requester wallet.
4. Both evaluate and pairwise jobs return typed solutions, preserving checkpoint metadata.
5. Cold-start succeeds at the configured local execution timeout; failures terminate
   without claiming a solution; compare hold and payout ledger records.
6. Ollama `chat.completions` traffic is unaffected; a CLEF-only worker never handles chat.

The included tests cover the local integration in isolation. **They do not
constitute a live Hub end-to-end test and do not prove settlement.** The actual
Hub server files (`stable_hub.py`, `stable_hub_worker_sessions.py`,
`viewport_routes_energy.py`) are unchanged by this patch.

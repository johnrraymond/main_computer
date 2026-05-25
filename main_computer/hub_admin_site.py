from __future__ import annotations

import html
from typing import Any

from main_computer.hub_security import HUB_SECURITY_PROFILE


HUB_ADMIN_ROUTES = {
    "/",
    "/admin",
    "/admin/",
    "/admin/hub",
    "/admin/hub/",
    "/hub",
    "/hub/",
    "/hub/admin",
    "/hub/admin/",
}


def build_admin_bootstrap_payload(
    *,
    config: Any,
    registry: Any,
    dispatcher: Any,
    energy_ledger: Any,
    credit_ledger: Any | None = None,
    credit_indexer: Any | None = None,
) -> dict[str, Any]:
    """Return a single dashboard-friendly snapshot for the hub admin/control site."""

    status = registry.status()
    metrics = dispatcher.metrics()
    credit_status = credit_ledger.status() if credit_ledger is not None else {"ok": False, "account_count": 0, "totals": {}}
    credit_indexer_status = credit_indexer.status() if credit_indexer is not None else {"ok": False}
    workers = [
        dict(worker)
        for worker in status.get("workers", [])
        if isinstance(worker, dict)
    ]
    requests = dispatcher.list_requests(limit=50)
    models = sorted(
        {
            str(model).strip()
            for worker in workers
            for model in (worker.get("models") if isinstance(worker.get("models"), list) else [worker.get("model", "")])
            if str(model).strip()
        }
    )
    if getattr(config, "model", "") and str(config.model) not in models:
        models.append(str(config.model))
        models.sort()
    return {
        "ok": True,
        "service": "main-computer-hub",
        "api_version": "v1",
        "admin_site": {
            "name": "Main Computer Hub Control",
            "routes": sorted(HUB_ADMIN_ROUTES),
            "poll_interval_ms": 5000,
            "local_admin_warning": "Bind the hub to loopback or put it behind your own auth/proxy before exposing the control site remotely.",
        },
        "hub": status.get("hub", {}),
        "status": status,
        "metrics": metrics,
        "workers": workers,
        "worker_count": len(workers),
        "requests": requests,
        "request_count": len(requests),
        "models": models,
        "security": {
            "high_security_default": bool(getattr(config, "hub_high_security", True)),
            "hub_blind_envelopes": bool(getattr(config, "hub_high_security", True)),
            "encryption_profile": HUB_SECURITY_PROFILE,
            "transport": "https-required-except-loopback",
            "allow_insecure_dev_network": bool(getattr(config, "hub_allow_insecure_dev_network", False)),
        },
        "energy": energy_ledger.status(),
        "credits": credit_status,
        "credit_indexer": credit_indexer_status,
        "endpoints": {
            "health": "/api/hub/v1/health",
            "status": "/api/hub/v1/status",
            "metrics": "/api/hub/v1/metrics",
            "workers": "/api/hub/v1/workers",
            "worker_register": "/api/hub/v1/workers/register",
            "models": "/api/hub/v1/models",
            "requests": "/api/hub/v1/requests",
            "payouts": "/api/hub/v1/payouts",
            "payout_claim": "/api/hub/v1/payouts/claim",
            "credits": "/api/hub/v1/credits",
            "credit_accounts": "/api/hub/v1/credits/accounts",
            "credit_balance": "/api/hub/v1/credits/balance",
            "credit_transactions": "/api/hub/v1/credits/transactions",
            "credit_purchases": "/api/hub/v1/credits/purchases",
            "credit_indexer": "/api/hub/v1/credits/indexer",
            "credit_purchase_import": "/api/hub/v1/credits/purchases/import",
            "credit_admin_issue": "/api/hub/v1/credits/admin/issue",
        },
    }


def render_hub_admin_html(*, service_name: str = "Main Computer Hub Control") -> str:
    title = html.escape(service_name)
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #071018;
      --panel: rgba(255,255,255,0.075);
      --panel-strong: rgba(255,255,255,0.12);
      --border: rgba(255,255,255,0.16);
      --text: #eef7ff;
      --muted: #9db4c7;
      --good: #71f2a4;
      --warn: #ffd37a;
      --bad: #ff8a8a;
      --accent: #82cfff;
      --shadow: 0 24px 60px rgba(0,0,0,0.35);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(60,130,255,0.18), transparent 32rem),
        radial-gradient(circle at 85% 10%, rgba(51,255,176,0.11), transparent 30rem),
        var(--bg);
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: flex-start;
      padding: 2rem clamp(1rem, 4vw, 3rem) 1rem;
    }}
    h1 {{ margin: 0; font-size: clamp(2rem, 4vw, 3.8rem); letter-spacing: -0.05em; }}
    h2 {{ margin: 0 0 1rem; font-size: 1.1rem; }}
    p {{ color: var(--muted); line-height: 1.55; }}
    button, input, select, textarea {{
      border: 1px solid var(--border);
      border-radius: 0.8rem;
      background: rgba(255,255,255,0.08);
      color: var(--text);
      padding: 0.7rem 0.85rem;
      font: inherit;
    }}
    button {{
      cursor: pointer;
      background: linear-gradient(180deg, rgba(130,207,255,0.24), rgba(130,207,255,0.11));
      box-shadow: 0 10px 28px rgba(0,0,0,0.18);
    }}
    button:hover {{ border-color: rgba(130,207,255,0.7); }}
    button.danger {{ background: rgba(255,138,138,0.16); }}
    main {{
      display: grid;
      gap: 1rem;
      padding: 0 clamp(1rem, 4vw, 3rem) 3rem;
    }}
    .toolbar {{ display: flex; align-items: center; justify-content: flex-end; gap: .75rem; flex-wrap: wrap; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: .35rem;
      padding: .4rem .65rem;
      border: 1px solid var(--border);
      border-radius: 999px;
      color: var(--muted);
      background: rgba(255,255,255,0.055);
      font-size: .86rem;
    }}
    .grid {{
      display: grid;
      gap: 1rem;
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 1.25rem;
      box-shadow: var(--shadow);
      padding: 1rem;
      backdrop-filter: blur(18px);
    }}
    .card-title {{ color: var(--muted); font-size: .86rem; margin-bottom: .35rem; }}
    .card-value {{ font-size: 2rem; font-weight: 750; letter-spacing: -0.04em; }}
    .two-col {{
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(20rem, .9fr);
      gap: 1rem;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: .72rem .55rem; border-bottom: 1px solid rgba(255,255,255,0.09); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; }}
    td {{ font-size: .92rem; }}
    code {{ color: #c5e8ff; }}
    .status-good {{ color: var(--good); }}
    .status-warn {{ color: var(--warn); }}
    .status-bad {{ color: var(--bad); }}
    .form-grid {{ display: grid; gap: .75rem; }}
    .form-row {{ display: grid; gap: .4rem; }}
    .form-row label {{ color: var(--muted); font-size: .84rem; }}
    textarea {{ min-height: 7rem; resize: vertical; }}
    .notice {{
      border-left: 3px solid var(--warn);
      padding: .75rem 1rem;
      background: rgba(255,211,122,.08);
      color: #ffe2a3;
      border-radius: .7rem;
    }}
    .log {{
      max-height: 16rem;
      overflow: auto;
      white-space: pre-wrap;
      color: var(--muted);
      font-size: .86rem;
    }}
    @media (max-width: 920px) {{
      header {{ flex-direction: column; }}
      .grid, .two-col {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <span class=\"badge\">Hub admin/control site</span>
      <h1>{title}</h1>
      <p>Operate worker registration, request routing, lifecycle visibility, and payout controls from the same server that exposes the hub API.</p>
    </div>
    <div class=\"toolbar\">
      <span id=\"lastUpdated\" class=\"badge\">not loaded</span>
      <button id=\"refreshBtn\" type=\"button\">Refresh</button>
    </div>
  </header>

  <main>
    <section class=\"notice\">
      Admin controls are served by the hub process. Bind to loopback or protect this site with your own auth/proxy before exposing it outside a trusted network.
    </section>

    <section class=\"grid\">
      <article class=\"panel\"><div class=\"card-title\">Workers</div><div id=\"workerCount\" class=\"card-value\">—</div></article>
      <article class=\"panel\"><div class=\"card-title\">Available</div><div id=\"availableCount\" class=\"card-value\">—</div></article>
      <article class=\"panel\"><div class=\"card-title\">Stale</div><div id=\"staleCount\" class=\"card-value\">—</div></article>
      <article class=\"panel\"><div class=\"card-title\">Tracked requests</div><div id=\"requestCount\" class=\"card-value\">—</div></article>
      <article class=\"panel\"><div class=\"card-title\">Compute Credit accounts</div><div id=\"creditAccountCount\" class=\"card-value\">—</div></article>
    </section>

    <section class=\"two-col\">
      <article class=\"panel\">
        <h2>Worker fleet</h2>
        <div style=\"overflow:auto\">
          <table>
            <thead><tr><th>Node</th><th>Status</th><th>Models</th><th>Capacity</th><th>Last seen</th></tr></thead>
            <tbody id=\"workersBody\"><tr><td colspan=\"5\">Loading…</td></tr></tbody>
          </table>
        </div>
      </article>

      <article class=\"panel\">
        <h2>Register worker</h2>
        <form id=\"registerForm\" class=\"form-grid\">
          <div class=\"form-row\"><label for=\"nodeId\">Node id</label><input id=\"nodeId\" name=\"node_id\" placeholder=\"gpu-worker-01\" required></div>
          <div class=\"form-row\"><label for=\"endpoint\">Endpoint</label><input id=\"endpoint\" name=\"endpoint\" placeholder=\"http://127.0.0.1:8771\" required></div>
          <div class=\"form-row\"><label for=\"model\">Model</label><input id=\"model\" name=\"model\" placeholder=\"llama3.1\"></div>
          <div class=\"form-row\"><label for=\"maxConcurrency\">Max concurrency</label><input id=\"maxConcurrency\" name=\"max_concurrency\" type=\"number\" min=\"1\" value=\"1\"></div>
          <button type=\"submit\">Register worker</button>
        </form>
      </article>
    </section>

    <section class=\"two-col\">
      <article class=\"panel\">
        <h2>Request lifecycle</h2>
        <div style=\"overflow:auto\">
          <table>
            <thead><tr><th>Request</th><th>State</th><th>Worker</th><th>Model</th><th>Updated</th><th>Control</th></tr></thead>
            <tbody id=\"requestsBody\"><tr><td colspan=\"6\">Loading…</td></tr></tbody>
          </table>
        </div>
      </article>

      <article class=\"panel\">
        <h2>Submit test AI request</h2>
        <form id=\"requestForm\" class=\"form-grid\">
          <div class=\"form-row\"><label for=\"requestModel\">Model</label><input id=\"requestModel\" name=\"model\" placeholder=\"optional model\"></div>
          <div class=\"form-row\"><label for=\"prompt\">Prompt</label><textarea id=\"prompt\" name=\"prompt\" required>Say hello from the hub control site.</textarea></div>
          <div class=\"form-row\"><label for=\"idempotencyKey\">Idempotency key</label><input id=\"idempotencyKey\" name=\"idempotency_key\" placeholder=\"optional-safe-retry-key\"></div>
          <button type=\"submit\">Submit request</button>
        </form>
      </article>
    </section>

    <section class=\"panel\">
      <h2>Compute Credits</h2>
      <p>Internal service-credit ledger for purchases, balances, and future request charging. This is off-chain hub accounting, not a public ERC-20 balance.</p>
      <div id=\"creditSummary\">Loading…</div>
    </section>

    <section class=\"two-col\">
      <article class=\"panel\">
        <h2>Security and energy</h2>
        <div id=\"securityEnergy\">Loading…</div>
      </article>
      <article class=\"panel\">
        <h2>Payout controls</h2>
        <form id=\"payoutForm\" class=\"form-grid\">
          <div class=\"form-row\"><label for=\"payoutNode\">Worker node id</label><input id=\"payoutNode\" name=\"node_id\" placeholder=\"gpu-worker-01\" required></div>
          <div class=\"form-row\"><label for=\"memo\">Memo</label><input id=\"memo\" name=\"memo\" placeholder=\"manual admin claim\"></div>
          <button type=\"submit\">Claim queued payouts</button>
        </form>
      </article>
    </section>

    <section class=\"panel\">
      <h2>Control log</h2>
      <div id=\"log\" class=\"log\">Ready.</div>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    const terminalStates = new Set(['completed', 'failed', 'cancelled', 'expired']);

    function setText(id, value) {{ $(id).textContent = value; }}
    function log(message) {{
      const now = new Date().toLocaleTimeString();
      $('log').textContent = `[${{now}}] ${{message}}\n` + $('log').textContent;
    }}
    async function api(path, options = {{}}) {{
      const response = await fetch(path, {{
        ...options,
        headers: {{'Content-Type': 'application/json', ...(options.headers || {{}})}}
      }});
      const text = await response.text();
      let data = {{}};
      try {{ data = text ? JSON.parse(text) : {{}}; }} catch (err) {{ data = {{error: text || String(err)}}; }}
      if (!response.ok || data.error) throw new Error(data.error || `${{response.status}} ${{response.statusText}}`);
      return data;
    }}
    function stateClass(value) {{
      const text = String(value || '').toLowerCase();
      if (['available','completed'].includes(text)) return 'status-good';
      if (['busy','dispatching','running','retrying','queued','configured'].includes(text)) return 'status-warn';
      if (['stale','failed','cancelled','expired','offline'].includes(text)) return 'status-bad';
      return '';
    }}
    function renderWorkers(workers) {{
      const body = $('workersBody');
      if (!workers.length) {{
        body.innerHTML = '<tr><td colspan=\"5\">No workers registered yet.</td></tr>';
        return;
      }}
      body.innerHTML = workers.map((worker) => `
        <tr>
          <td><code>${{escapeHtml(worker.node_id || '')}}</code></td>
          <td class=\"${{stateClass(worker.status)}}\">${{escapeHtml(worker.status || '')}}${{worker.stale ? ' / stale' : ''}}</td>
          <td>${{escapeHtml((worker.models || [worker.model || '']).filter(Boolean).join(', ') || '—')}}</td>
          <td>${{Number(worker.active_requests || 0)}} / ${{Number(worker.max_concurrency || 1)}} active · q=${{Number(worker.queue_depth || 0)}}</td>
          <td>${{escapeHtml(worker.last_seen_at || '—')}}</td>
        </tr>`).join('');
    }}
    function renderRequests(requests) {{
      const body = $('requestsBody');
      if (!requests.length) {{
        body.innerHTML = '<tr><td colspan=\"6\">No requests tracked yet.</td></tr>';
        return;
      }}
      body.innerHTML = requests.map((request) => {{
        const id = request.request_id || '';
        const state = request.state || request.status || '';
        const control = terminalStates.has(String(state).toLowerCase())
          ? '<span class=\"badge\">final</span>'
          : `<button class=\"danger\" data-cancel=\"${{escapeAttr(id)}}\" type=\"button\">Cancel</button>`;
        return `
          <tr>
            <td><code>${{escapeHtml(id)}}</code></td>
            <td class=\"${{stateClass(state)}}\">${{escapeHtml(state)}}</td>
            <td>${{escapeHtml(request.worker_node_id || request.requested_worker_node_id || '—')}}</td>
            <td>${{escapeHtml(request.model || '—')}}</td>
            <td>${{escapeHtml(request.updated_at || request.created_at || '—')}}</td>
            <td>${{control}}</td>
          </tr>`;
      }}).join('');
      body.querySelectorAll('[data-cancel]').forEach((button) => {{
        button.addEventListener('click', async () => {{
          const id = button.getAttribute('data-cancel');
          try {{
            await api(`/api/hub/v1/requests/${{encodeURIComponent(id)}}/cancel`, {{method: 'POST', body: JSON.stringify({{}})}});
            log(`Cancelled request ${{id}}`);
            await refresh();
          }} catch (err) {{
            log(`Cancel failed: ${{err.message}}`);
          }}
        }});
      }});
    }}
    function renderCredits(data) {{
      const credits = data.credits || {{}};
      const indexer = data.credit_indexer || {{}};
      const totals = credits.totals || {{}};
      $('creditSummary').innerHTML = `
        <p><strong>Unit:</strong> ${{escapeHtml((credits.unit && credits.unit.name) || 'Compute Credits')}}</p>
        <p><strong>Accounts:</strong> ${{Number(credits.account_count || 0)}}</p>
        <p><strong>Available:</strong> ${{Number(totals.available_credits || 0)}}</p>
        <p><strong>Held:</strong> ${{Number(totals.held_credits || 0)}}</p>
        <p><strong>Purchased:</strong> ${{Number(totals.purchased_credits || 0)}}</p>
        <p><strong>Transactions:</strong> ${{Number(credits.transaction_count || 0)}}</p>
        <p><strong>Indexer:</strong> ${{escapeHtml(indexer.phase || 'not configured')}} / ${{escapeHtml(indexer.mode || 'manual')}}</p>
      `;
    }}
    function renderSecurityEnergy(data) {{
      const security = data.security || {{}};
      const energy = data.energy || {{}};
      $('securityEnergy').innerHTML = `
        <p><strong>High security:</strong> ${{security.high_security_default ? 'enabled' : 'disabled'}}</p>
        <p><strong>Encryption:</strong> ${{escapeHtml(security.encryption_profile || 'unknown')}}</p>
        <p><strong>Transport:</strong> ${{escapeHtml(security.transport || 'unknown')}}</p>
        <p><strong>Ledger:</strong> ${{escapeHtml(JSON.stringify(energy))}}</p>
      `;
    }}
    async function refresh() {{
      try {{
        const data = await api('/api/hub/v1/admin/bootstrap');
        const status = data.status || {{}};
        const metrics = data.metrics || {{}};
        const requests = data.requests || [];
        setText('workerCount', status.worker_count ?? data.worker_count ?? '0');
        setText('availableCount', status.available_worker_count ?? metrics.available_worker_count ?? '0');
        setText('staleCount', status.stale_worker_count ?? metrics.stale_worker_count ?? '0');
        setText('requestCount', data.request_count ?? requests.length ?? '0');
        setText('creditAccountCount', (data.credits && data.credits.account_count) ?? '0');
        renderWorkers(data.workers || []);
        renderRequests(requests);
        renderCredits(data);
        renderSecurityEnergy(data);
        setText('lastUpdated', 'updated ' + new Date().toLocaleTimeString());
      }} catch (err) {{
        log('Refresh failed: ' + err.message);
        setText('lastUpdated', 'refresh failed');
      }}
    }}
    function escapeHtml(value) {{
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char]));
    }}
    function escapeAttr(value) {{ return escapeHtml(value).replace(/`/g, '&#96;'); }}

    $('refreshBtn').addEventListener('click', refresh);
    $('registerForm').addEventListener('submit', async (event) => {{
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = Object.fromEntries(form.entries());
      payload.max_concurrency = Number(payload.max_concurrency || 1);
      try {{
        await api('/api/hub/v1/workers/register', {{method: 'POST', body: JSON.stringify(payload)}});
        log(`Registered worker ${{payload.node_id}}`);
        event.currentTarget.reset();
        $('maxConcurrency').value = '1';
        await refresh();
      }} catch (err) {{
        log('Worker registration failed: ' + err.message);
      }}
    }});
    $('requestForm').addEventListener('submit', async (event) => {{
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = Object.fromEntries(form.entries());
      payload.messages = [{{role: 'user', content: payload.prompt || ''}}];
      delete payload.prompt;
      if (!payload.model) delete payload.model;
      if (!payload.idempotency_key) delete payload.idempotency_key;
      try {{
        const data = await api('/api/hub/v1/requests', {{method: 'POST', body: JSON.stringify(payload)}});
        log(`Submitted request ${{data.request && data.request.request_id ? data.request.request_id : ''}}`);
        await refresh();
      }} catch (err) {{
        log('Request submit failed: ' + err.message);
      }}
    }});
    $('payoutForm').addEventListener('submit', async (event) => {{
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
      try {{
        const data = await api('/api/hub/v1/payouts/claim', {{method: 'POST', body: JSON.stringify(payload)}});
        log('Payout claim response: ' + JSON.stringify(data));
        await refresh();
      }} catch (err) {{
        log('Payout claim failed: ' + err.message);
      }}
    }});
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""

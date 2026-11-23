// Read-only network evidence for the disposable Worker's seller-settings save.
// Never records raw HTTP bodies, wallet credentials, arbitrary values or URLs.
const allowedFields = new Set([
  'sellerCreditsPerToken', 'sellerTargetTokens', 'sellerEnabled',
  'rentalEnabled', 'sellerAvailabilityMode'
]);
const decimal = value => /^(?:0|[1-9]\d*)(?:\.\d{1,18})?$/.test(String(value))
  ? String(value).slice(0, 32) : 'unknown';
const tokens = value => Number.isSafeInteger(value) && value >= 1 && value <= 128000
  ? value : null;
const settingsPath = url => {
  try { return new URL(url).pathname === '/api/applications/worker/settings'; }
  catch { return false; }
};
export function createWorkerSettingsWriteTrace(now = () => Date.now()) {
  const startedAt = now();
  const rows = [];
  const requests = new WeakMap();
  const pending = new Set();
  let nextId = 1;
  function onRequest(request) {
    try {
      if (!settingsPath(request.url()) || request.method() !== 'POST') return;
      const body = request.postDataJSON();
      const changed = Array.isArray(body?.changed_fields)
        ? [...new Set(body.changed_fields.filter(name => allowedFields.has(name)))].sort()
        : ['full-replacement'];
      const row = {id: nextId++, ms: Math.max(0, now() - startedAt),
        fields: changed, requestRate: decimal(body?.settings?.sellerCreditsPerToken),
        requestTokens: tokens(body?.settings?.sellerTargetTokens),
        http: null, responseRate: 'unknown', responseTokens: null,
        responseOk: null, responseMs: null};
      requests.set(request, row);
      if (rows.length < 48) rows.push(row);
    } catch { /* never expose malformed request bodies */ }
  }
  function onResponse(response) {
    try {
      const row = requests.get(response.request());
      if (!row) return;
      row.http = response.status();
      row.responseMs = Math.max(0, now() - startedAt);
      let task;
      task = Promise.resolve().then(() => response.json()).then(body => {
        row.responseOk = body?.ok === true;
        row.responseRate = decimal(body?.settings?.sellerCreditsPerToken);
        row.responseTokens = tokens(body?.settings?.sellerTargetTokens);
      }).catch(() => { row.responseOk = false; }).finally(() => pending.delete(task));
      pending.add(task);
    } catch { /* classification is best effort; no raw text */ }
  }
  function attach(page) {
    page.on('request', onRequest);
    page.on('response', onResponse);
    return () => {
      page.off('request', onRequest);
      page.off('response', onResponse);
    };
  }
  async function finish() {
    await Promise.race([Promise.allSettled([...pending]),
      new Promise(resolve => setTimeout(resolve, 1500))]);
    return rows.map(row => ({...row, fields: [...row.fields]}));
  }
  return {attach, finish, rows};
}

'use strict';
// Exercise the actual Worker application's RPC proof / retry code, rather than
// a reimplementation. No extension, wallet secret, RPC, or external network.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');

const file = path.resolve(__dirname, '../main_computer/web/applications/scripts/worker.js');
const source = fs.readFileSync(file, 'utf8');
const start = source.indexOf('function workerWalletIsRpcEndpointBackoff(');
const end = source.indexOf('async function workerRequestDevWalletChainUpdate(', start);
assert.ok(start >= 0 && end > start, 'RPC transition functions must exist in actual Worker code');
const implementation = source.slice(start, end);

const forbidden = [
  'wallet_addEthereumChain', 'wallet_switchEthereumChain',
  'eth_requestAccounts', 'eth_sendTransaction', 'personal_sign'
];

function environment({blocks = [], chains = [], waitCost = 100} = {}) {
  let tick = 0;
  let blockCalls = 0;
  let chainCalls = 0;
  let rebuilt = 0;
  const events = [];
  const calledMethods = [];
  const messages = [];
  const inputs = {blocks: [...blocks], chains: [...chains]};
  const context = {
    Date: {now: () => tick},
    setTimeout: (fn, ms) => { tick += Math.max(waitCost, ms); queueMicrotask(fn); },
    Math, Number, String, RegExp, Set, Promise, Boolean,
    WORKER_METAMASK_RPC_BACKOFF_TIMEOUT_MS: 75000,
    WORKER_METAMASK_RPC_BACKOFF_POLL_MS: 3000,
    workerSaveStatus: {textContent: ''},
    workerSelectedWalletChainIdHex: () => '0x28757b1',
    workerSelectedWalletChainName: () => 'Main Computer Testnet',
    workerSelectedWalletRpcUrl: () => 'https://testnet-rpc.greatlibrary.io',
    workerNormalizeChainIdHex: value => String(value || '').toLowerCase(),
    workerWalletErrorMessage: error => String(error?.message || error || ''),
    workerErrorCode: error => {
      const pending = [error];
      const visited = new Set();
      while (pending.length) {
        const v = pending.shift();
        if (!v || typeof v !== 'object' || visited.has(v)) continue;
        visited.add(v);
        if (v.code !== undefined && v.code !== null) return String(v.code);
        for (const key of ['error', 'info', 'data', 'cause']) {
          if (v[key] && typeof v[key] === 'object') pending.push(v[key]);
        }
      }
      return '';
    },
    workerWalletRecordEvent: (type, detail) => events.push({type, detail}),
    workerSleep: async ms => {tick += Math.max(waitCost, ms);},
    workerRebuildWalletBrowserProvider: () => ({generation: ++rebuilt}),
    workerProviderStateFlag: () => null,
    workerBrowserProviderSend: async (provider, method) => {
      calledMethods.push(method);
      assert.equal(forbidden.includes(method), false, `forbidden mutation ${method}`);
      if (method === 'eth_chainId') {
        const next = inputs.chains[chainCalls++];
        return next === undefined ? '0x28757b1' : next;
      }
      if (method === 'eth_blockNumber') {
        const next = inputs.blocks[blockCalls++];
        if (next instanceof Error) throw next;
        return next === undefined ? '0xad26' : next;
      }
      throw Error('unexpected provider call ' + method);
    },
    console: {log: (...items) => messages.push(items)}
  };
  const exported = vm.runInNewContext(
    implementation + ';({workerWalletIsFreshNetworkRpcRejection, workerProveInjectedProviderRpc, workerProveInjectedProviderRpcWithBackoff})',
    context, {filename: 'actual-worker-rpc-transition.js'}
  );
  return {
    ...exported,
    context, events, calledMethods,
    get blockCalls() {return blockCalls;},
    get rebuilt() {return rebuilt;},
    get tick() {return tick;},
  };
}

function rpc403() {
  const error = new Error('could not coalesce error (error={ "code": -32080, "data": { "cause": null, "httpStatus": 403 }, "message": "RPC endpoint returned HTTP client error" })');
  error.code = 'UNKNOWN_ERROR';
  error.error = {code: -32080, data: {httpStatus: 403}};
  return error;
}

test('retries a fresh-network HTTP 403 and proves block via rebuilt provider', async () => {
  const h = environment({blocks: [rpc403(), '0xad5e']});
  const result = await h.workerProveInjectedProviderRpcWithBackoff({generation: 0}, {
    timeoutMs: 600, pollMs: 100, reason: 'connect-pre-account',
    ethers: {BrowserProvider() {}}, injectedProvider: {isMetaMask: true}
  });
  assert.equal(result.ok, true);
  assert.equal(result.chainId, '0x28757b1');
  assert.equal(result.blockNumber, '0xad5e');
  assert.equal(result.attempts, 2);
  assert.equal(h.rebuilt, 1);
  assert.equal(result.browserProvider.generation, 1);
  assert.ok(h.events.some(e => e.type === 'connect.wallet.rpcProof.backoffWait' && e.detail.failureKind === 'fresh-network-http-403'));
  assert.ok(h.events.some(e => e.type === 'connect.wallet.rpcProof.backoffCleared'));
});

test('persistent HTTP 403 exhausts the finite budget and never connects', async () => {
  const h = environment({blocks: Array.from({length: 10}, rpc403)});
  const result = await h.workerProveInjectedProviderRpcWithBackoff({}, {timeoutMs: 350, pollMs: 100});
  assert.equal(result.ok, false);
  assert.equal(result.backoffExpired, true);
  assert.ok(result.attempts >= 2 && result.attempts <= 5);
  assert.match(result.reason, /failed after .* attempts/);
});

test('user rejection is not retried', async () => {
  const h = environment({blocks: [Object.assign(new Error('User rejected'), {code: 4001}), '0xad5e']});
  const result = await h.workerProveInjectedProviderRpcWithBackoff({}, {timeoutMs: 300, pollMs: 100});
  assert.equal(result.ok, false);
  assert.equal(result.attempts, 1);
  assert.equal(h.blockCalls, 1);
});

test('non-transient RPC error is not retried', async () => {
  const h = environment({blocks: [new Error('invalid json-rpc response'), '0xad5e']});
  const result = await h.workerProveInjectedProviderRpcWithBackoff({}, {timeoutMs: 300, pollMs: 100});
  assert.equal(result.ok, false);
  assert.equal(h.blockCalls, 1);
});

test('wrong chain fails without reading a block or retrying', async () => {
  const h = environment({chains: ['0x28757b2']});
  const result = await h.workerProveInjectedProviderRpcWithBackoff({}, {timeoutMs: 300, pollMs: 100});
  assert.equal(result.ok, false);
  assert.equal(result.wrongChain, true);
  assert.equal(h.blockCalls, 0);
});

test('chain switches during block read: reject proof', async () => {
  const h = environment({blocks: ['0xad5e'], chains: ['0x28757b1', '0x28757b2']});
  const result = await h.workerProveInjectedProviderRpcWithBackoff({}, {timeoutMs: 300, pollMs: 100});
  assert.equal(result.ok, false);
  assert.equal(result.wrongChain, true);
  assert.equal(h.blockCalls, 1);
});

test('invalid block number is refused', async () => {
  for (const invalid of ['5', null, '', '0xgg']) {
    const h = environment({blocks: [invalid]});
    const result = await h.workerProveInjectedProviderRpcWithBackoff({}, {timeoutMs: 300, pollMs: 100});
    assert.equal(result.ok, false, `invalid block ${invalid} must fail`);
    assert.equal(result.attempts, 1);
  }
});

test('provider backoff remains retryable', async () => {
  const h = environment({blocks: [new Error('RPC endpoint returned too many errors. Retrying in 3 seconds'), '0xad5e']});
  const result = await h.workerProveInjectedProviderRpcWithBackoff({}, {timeoutMs: 300, pollMs: 100});
  assert.equal(result.ok, true);
  assert.equal(result.attempts, 2);
});

test('retry scope restricted to repaired-network path', () => {
  const ensureStart = source.indexOf('async function workerEnsureDevWalletChain(');
  const ensureEnd = source.indexOf('async function workerReuseSatisfiedWalletNetworkPreflight(', ensureStart);
  const ensure = source.slice(ensureStart, ensureEnd);
  assert.match(ensure, /needsUpdate\s*\?\s*await workerProveInjectedProviderRpcWithBackoff\(/);
  assert.match(ensure, /:\s*await workerProveInjectedProviderRpc\(activeBrowserProvider\)/);
  assert.match(ensure, /activeBrowserProvider = proof\.browserProvider \|\| activeBrowserProvider/);
  assert.match(ensure, /if \(!proof\.ok\)\s*\{\s*throw new Error/);
});

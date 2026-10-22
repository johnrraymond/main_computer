"""Exercise the real browser signing function with a mocked EIP-1193 provider.

No wallet, RPC, Hub or network access is required. The Node VM runs the same
wallet.js that is included in the Worker application.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


WALLET_JS = Path(__file__).resolve().parents[1] / "main_computer/web/applications/scripts/wallet.js"
WORKER_JS = Path(__file__).resolve().parents[1] / "main_computer/web/applications/scripts/worker.js"
ADDRESS = "0x0960ae2272cc522e1c7cb8ce7f3d0603cf99d8c4"
NODE = shutil.which("node")


_BROWSER_PROBE = r"""
const fs = require('fs');
const vm = require('vm');
const settings = JSON.parse(process.argv[1]);
const scriptPath = process.argv[2];
let activeChain = BigInt(settings.actualChain);
let activeAccount = settings.actualAccount || settings.walletAddress;
let signCount = 0;
let mutationCount = 0;
let signedMessage = null;
class BrowserProvider {
  async getSigner() {
    return {
      getAddress: async () => activeAccount,
      signMessage: async (message) => {
        signCount++;
        signedMessage = JSON.parse(message);
        if (settings.changeChainDuringSign) activeChain = BigInt(settings.changeChainDuringSign);
        if (settings.changeAccountDuringSign) activeAccount = settings.changeAccountDuringSign;
        return '0x' + '12'.repeat(65);
      }
    };
  }
  async getNetwork() { return {chainId: activeChain}; }
  async send() { mutationCount++; throw new Error('Forbidden network mutation'); }
}
const window = {
  crypto: {randomUUID: () => 'deterministic-msk-id'},
  location: {origin: 'http://127.0.0.1:8765'},
  ethereum: {request: async () => { mutationCount++; throw new Error('Forbidden chain switch'); }},
  ethers: {BrowserProvider},
};
const context = vm.createContext({window, TextEncoder, Date, Math, console, BigInt});
vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, {filename: 'wallet.js'});
(async () => {
  let result = null, error = null;
  try {
    result = await window.MainComputerWalletLibrary.requestMultiSessionKeySignature({
      requestContext: {wallet_address: settings.walletAddress, chain_id: settings.expectedChain},
      origin: window.location.origin
    });
  } catch (exc) { error = String(exc.message || exc); }
  process.stdout.write(JSON.stringify({
    result, error, signCount, mutationCount, signedMessage,
    finalChain: '0x' + activeChain.toString(16)
  }));
})().catch(exc => {console.error(exc); process.exit(1)});
"""


def _probe(**updates: object) -> dict:
    if not NODE:
        pytest.skip("Node.js is required to run the actual browser signing code")
    config: dict[str, object] = {
        "actualChain": "0x28757b1",
        "expectedChain": "0x28757b1",
        "walletAddress": ADDRESS,
    }
    config.update(updates)
    proc = subprocess.run(
        [NODE, "-e", _BROWSER_PROBE, json.dumps(config), str(WALLET_JS)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("chain", ["0x28757b1", "0x28757b0", "0x28757b2"])
def test_signs_exact_selected_chain_without_switching(chain: str) -> None:
    result = _probe(actualChain=chain, expectedChain=chain)
    assert result["error"] is None
    assert result["mutationCount"] == 0
    assert result["signCount"] == 1
    assert result["result"]["chain_id"] == chain
    assert result["result"]["message"]["chain_id"] == chain
    assert result["signedMessage"]["chain_id"] == chain
    assert result["result"]["message"]["wallet_address"] == ADDRESS


def test_rejects_wrong_network_without_signing_or_switching() -> None:
    result = _probe(actualChain="0x28757b2", expectedChain="0x28757b1")
    assert "Worker requires 0x28757b1" in result["error"]
    assert result["signCount"] == 0
    assert result["mutationCount"] == 0


@pytest.mark.parametrize("chain", ["", "garbage", "0x0"])
def test_missing_or_invalid_requested_chain_fails_closed(chain: str) -> None:
    result = _probe(expectedChain=chain)
    assert "explicit valid Worker chain_id" in result["error"]
    assert result["signCount"] == 0
    assert result["mutationCount"] == 0


def test_wallet_address_mismatch_rejected_before_signing() -> None:
    result = _probe(actualAccount="0x" + "1" * 40)
    assert "does not match Worker request" in result["error"]
    assert result["signCount"] == 0
    assert result["mutationCount"] == 0


def test_network_change_during_signature_discards_authorization() -> None:
    result = _probe(changeChainDuringSign="0x28757b2")
    assert "changed during multi-session key signing" in result["error"]
    assert result["result"] is None
    assert result["mutationCount"] == 0


def test_account_change_during_signature_discards_authorization() -> None:
    result = _probe(changeAccountDuringSign="0x" + "2" * 40)
    assert "changed during multi-session key signing" in result["error"]
    assert result["result"] is None
    assert result["mutationCount"] == 0


def test_worker_passes_selected_connected_chain_to_signer() -> None:
    source = WORKER_JS.read_text(encoding="utf-8")
    assert "chain_id: workerNormalizeChainIdHex(wallet.chainId)" in source
    assert source.count("walletLibrary.requestMultiSessionKeySignature({") == 2
    assert source.count("requestContext,\n          origin:") == 2

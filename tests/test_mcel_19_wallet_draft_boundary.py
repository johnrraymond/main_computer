from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"


def read_script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_19a_wallet_smoke_guard_freezes_18n_no_mutation_baseline() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "mcelWallet19aSmokeRegressionGuard",
        "mcelWallet19aSmokeRegressionGuard.v1",
        'smokeVersion: "19A-MCEL"',
        "passed-no-mutation",
        "wallet-board-renders",
        "wallet-lifecycle-receipts-visible",
        "proof-dock-wallet-specimens-present",
        "tx-draft-identity-envelope-present",
        "tx-draft-validity-envelope-present",
        "provider-mutation-flags-false",
        "preflight-stays-no-mutation",
        "wallet19aSmokeRegressionGuard",
        "walletSmokeRegressionGuard",
    ]
    for marker in markers:
        assert marker in lab

    dangerous_methods = [
        "eth_sendTransaction",
        "eth_signTransaction",
        "personal_sign",
        "signTypedData",
        "sendTransaction",
        "broadcastTransaction",
    ]
    for method in dangerous_methods:
        assert method not in lab
        assert method not in studio

    assert re.search(r"canSend\s*:\s*false", lab)
    assert re.search(r"canSign\s*:\s*false", lab)
    assert re.search(r"canBroadcast\s*:\s*false", lab)
    assert "mutationExecuted: false" in lab
    assert lab.index("function mcelWallet19aSmokeRegressionGuard") < lab.index("function mcelWalletNegativePathTestWall")
    assert lab.index("boundary.wallet19aSmokeRegressionGuard = mcelWallet19aSmokeRegressionGuard(boundary)") < lab.index("boundary.wallet18nCompletionReport = mcelWallet18nCompletionReport(boundary)")


def test_19b_txdraft_identity_envelope_binds_source_wallet_chain_calldata_and_probe() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "mcelTinyContractSelectedReleaseSnapshot",
        "mcelTinyContractSelectedReleaseHash",
        "mcelTinyContractProbeEvidenceHash",
        "mcelTinyContractTxDraftIdentityEnvelope",
        "mcelWalletTxDraftIdentityEnvelope.v1",
        'identityVersion: "19B-MCEL"',
        "txDraftId",
        "txDraftHash",
        "selectedReleaseHash",
        "sourceRequestHash",
        "walletAccountHash",
        "chainProof",
        "calldataHash",
        "draftBuiltAt",
        "draftExpiresAt",
        "draftTtlMs",
        "probeEnvelopeIds",
        "probeEvidenceHash",
        "mutationLocked: true",
        '"runtime.txDraftIdentity"',
        "txDraftIdentity: null",
    ]
    for marker in markers:
        assert marker in lab

    manifest_index = lab.index("function mcelTinyContractScmManifest")
    manifest = lab[manifest_index:manifest_index + 5000]
    for owned_path in [
        '"txDraftIdentity"',
        '"txDraftValidity"',
        '"walletCommitBoundary"',
        '"walletSmokeRegressionGuard"',
    ]:
        assert owned_path in manifest

    draft_index = lab.index('"release.draftTx": {')
    draft_block = lab[draft_index:draft_index + 9000]
    assert '"source.devRelease.devNetwork"' in draft_block
    assert '"runtime.txDraftIdentity"' in draft_block
    assert '"runtime.txDraftValidity"' in draft_block
    assert "const txDraftIdentity = mcelTinyContractTxDraftIdentityEnvelope" in draft_block
    assert "ctx.set(\"runtime.txDraftIdentity\", txDraftIdentity)" in lab
    assert "ctx.set(\"runtime.txDraftValidity\", txDraftValidity)" in lab


def test_19c_txdraft_validity_enforces_stale_and_invalid_contexts() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "mcelTinyContractTxDraftValidityEnvelope",
        "mcelWalletTxDraftValidityEnvelope.v1",
        'validityVersion: "19C-MCEL"',
        "tx-draft-identity-changed",
        "selected-release-changed",
        "source-request-changed",
        "account-changed",
        "chain-changed",
        "calldata-changed",
        "probe-evidence-changed",
        "draft-age-expired",
        "no-send-boundary-missing",
        "txDraftValidityStatus",
        "validityEnvelope",
        "currentIdentity",
        "currentSelectedReleaseHash",
        "currentSourceRequestHash",
        "currentWalletAccountHash",
        "currentChainProof",
        "currentCalldataHash",
        "currentProbeEvidenceHash",
        "rebuild draft from current wallet/source/chain/probe context",
        "valid txDraft does not unlock send/sign/broadcast",
    ]
    for marker in markers:
        assert marker in lab

    freshness_index = lab.index("function mcelTinyContractTxDraftFreshnessCheck")
    freshness_block = lab[freshness_index:freshness_index + 10000]
    assert "const validityEnvelope = mcelTinyContractTxDraftValidityEnvelope" in freshness_block
    assert "invalidations.push(...validityEnvelope.invalidatedBy)" in freshness_block
    assert "validityEnvelope," in freshness_block

    apply_index = lab.index("function mcelTinyContractApplyTxDraftFreshness")
    apply_block = lab[apply_index:apply_index + 2500]
    assert "validityEnvelope: freshness.validityEnvelope" in apply_block
    assert "txDraftValidityStatus" in apply_block

    enforce_index = lab.index("function mcelTinyContractEnforceTxDraftProvenance")
    enforce_block = lab[enforce_index:enforce_index + 1500]
    assert "instance.runtime.txDraftValidity" in enforce_block
    assert "instance.runtime.txDraftIdentity" in enforce_block

    assert lab.index("function mcelTinyContractTxDraftIdentityEnvelope") < lab.index("function mcelTinyContractTxDraftValidityEnvelope")

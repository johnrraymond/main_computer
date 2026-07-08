from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
APPS = ROOT / "main_computer" / "web" / "applications" / "apps"


def read_script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def read_app(name: str) -> str:
    return (APPS / name).read_text(encoding="utf-8")


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
        "eth_signTransaction",
        "personal_sign",
        "broadcastTransaction",
    ]
    for method in dangerous_methods:
        assert method not in lab
        assert method not in studio

    assert "eth_sendTransaction" in lab
    assert "function mcelWallet21aPolicyBoundSendGate" in lab
    assert "eth_sendTransaction" not in studio
    assert not re.search(r"\.sendTransaction\s*\(", lab)
    assert not re.search(r"\.sendTransaction\s*\(", studio)

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


def test_19abc_visible_wallet_board_surfaces_guard_identity_and_validity() -> None:
    lab = read_script("mcel-lab.js")
    html = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-19abc-wallet-visible-board="true"',
        "18N-K complete locked wallet boundary + 19A/B/C/D/E draft proof",
        'id="mcel-19a-wallet-smoke-visible-status"',
        'id="mcel-19b-wallet-tx-draft-identity-visible-status"',
        'id="mcel-19c-wallet-tx-draft-validity-visible-status"',
        'id="mcel-19c-wallet-tx-draft-invalidated-by"',
        "19A guard",
        "19B identity",
        "19C validity",
        "Invalidated by",
    ]
    for marker in html_markers:
        assert marker in html

    lab_markers = [
        'document.querySelector("#mcel-19a-wallet-smoke-visible-status")',
        'document.querySelector("#mcel-19b-wallet-tx-draft-identity-visible-status")',
        'document.querySelector("#mcel-19c-wallet-tx-draft-validity-visible-status")',
        'document.querySelector("#mcel-19c-wallet-tx-draft-invalidated-by")',
        "txDraftIdentityEnvelope",
        "txDraftValidityEnvelope",
        "txDraftInvalidationReasons",
        "19A no-mutation smoke guard",
        "19B bind txDraft identity",
        "19C validate txDraft context",
        "19C says",
        "provider mutation remains locked",
    ]
    for marker in lab_markers:
        assert marker in lab

    render_index = lab.index("function renderMcel18nWalletToolSurface")
    render_block = lab[render_index:render_index + 36000]
    assert "visible19aSmokeSlot.textContent" in render_block
    assert "visible19bIdentitySlot.textContent" in render_block
    assert "visible19cValiditySlot.textContent" in render_block
    assert "visible19cInvalidatedSlot.textContent" in render_block
    assert "txDraftIdentityEnvelope," in render_block
    assert "txDraftValidityEnvelope," in render_block
    assert "...txDraftInvalidationReasons" in render_block


def test_19d_proof_surface_alignment_unifies_wallet_receipt_and_code_studio() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    html = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-19de-wallet-proof-alignment="true"',
        "18N-K complete locked wallet boundary + 19A/B/C/D/E draft proof",
        'id="mcel-19d-wallet-proof-alignment-visible-status"',
        "19D alignment",
    ]
    for marker in html_markers:
        assert marker in html

    lab_markers = [
        "mcelWallet19dCanonicalDraftProof",
        "mcelWallet19dCanonicalDraftProof.v1",
        "mcelWallet19dProofSurfaceAlignment",
        "mcelWallet19dProofSurfaceAlignment.v1",
        'alignmentVersion: "19D-MCEL"',
        "aligned-locked",
        "wallet-board-canonical-status-present",
        "tx-draft-proof-json-carries-identity-and-validity",
        "preflight-json-carries-identity-and-validity",
        "copied-receipt-carries-identity-and-validity",
        "proof-dock-wallet-specimens-align-with-locked-mutation",
        "code-studio-summary-ready-for-19d-fields",
        "wallet19dProofSurfaceAlignment",
        "proofSurfaceAlignment",
        "19D align proof surfaces",
        "19D says",
    ]
    for marker in lab_markers:
        assert marker in lab

    studio_markers = [
        "txDraftIdentityEnvelope",
        "txDraftValidityEnvelope",
        "txDraftValidityStatus",
        "txDraftInvalidationReasons",
        "wallet19dProofSurfaceAlignment",
        "wallet19dProofSurfaceAlignmentStatus",
        "proofSurfaceAlignment",
        "19D carries txDraft identity/validity into Code Studio proof dock summaries.",
    ]
    for marker in studio_markers:
        assert marker in studio

    preflight_index = lab.index("function mcelWalletPreflightReport")
    preflight_block = lab[preflight_index:preflight_index + 3000]
    assert "txDraftIdentityEnvelope: txDraftIdentityEnvelope || walletTxDraft.identityEnvelope || null" in preflight_block
    assert "txDraftValidityEnvelope: txDraftValidityEnvelope || walletTxDraft.validityEnvelope || walletFreshnessSnapshot.validityEnvelope || null" in preflight_block
    assert "txDraftInvalidationReasons" in preflight_block

    boundary_index = lab.index("function mcelWalletToolCommitBoundary")
    boundary_block = lab[boundary_index:boundary_index + 9000]
    assert "boundary.wallet19dProofSurfaceAlignment = mcelWallet19dProofSurfaceAlignment(boundary)" in boundary_block
    assert "boundary.wallet19dProofSurfaceAlignment" in boundary_block


def test_19e_negative_path_regression_covers_stale_contexts_without_unlocking_mutation() -> None:
    lab = read_script("mcel-lab.js")
    html = read_app("mcel-lab.html")
    studio = read_script("code-editor-mcel-studio.js")

    html_markers = [
        'id="mcel-19e-wallet-negative-path-visible-status"',
        "19E negative paths",
        "19E guard negative paths",
    ]
    for marker in html_markers:
        assert marker in html or marker in lab

    lab_markers = [
        "mcelWallet19eNegativePathRegression",
        "mcelWallet19eNegativePathRegression.v1",
        'regressionVersion: "19E-MCEL"',
        "passed-no-mutation-regression",
        "account-change-invalidates-draft",
        "chain-change-invalidates-draft",
        "source-request-change-invalidates-draft",
        "selected-release-change-invalidates-draft",
        "probe-evidence-change-invalidates-draft",
        "draft-age-expired-invalidates-draft",
        "invalid-draft-keeps-send-sign-broadcast-locked",
        "covered-by-validator",
        "observed-invalidated",
        "mcelTinyContractTxDraftValidityEnvelope",
        "wallet19eNegativePathRegression",
        "mutationExecuted: false",
    ]
    for marker in lab_markers:
        assert marker in lab

    for invalidation in [
        "account-changed",
        "chain-changed",
        "source-request-changed",
        "selected-release-changed",
        "probe-evidence-changed",
        "draft-age-expired",
    ]:
        assert invalidation in lab

    assert "wallet19eNegativePathRegressionStatus" in studio
    assert "19E negative-path regression remains locked and no-mutation." in studio

    regression_index = lab.index("function mcelWallet19eNegativePathRegression")
    regression_block = lab[regression_index:regression_index + 9000]
    assert "canSend: false" in regression_block
    assert "canSign: false" in regression_block
    assert "canBroadcast: false" in regression_block
    assert "mutationExecuted: false" in regression_block

    dangerous_methods = [
        "eth_signTransaction",
        "personal_sign",
        "broadcastTransaction",
    ]
    for method in dangerous_methods:
        assert method not in lab
        assert method not in studio

    assert "eth_sendTransaction" in lab
    assert "function mcelWallet21aPolicyBoundSendGate" in lab
    assert "eth_sendTransaction" not in studio
    assert not re.search(r"\.sendTransaction\s*\(", lab)
    assert not re.search(r"\.sendTransaction\s*\(", studio)

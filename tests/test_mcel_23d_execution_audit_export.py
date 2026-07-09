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


def test_23d_execution_audit_export_contracts_hash_chain_and_redaction_exist() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "mcelWallet23dExecutionAuditExport",
        "mcelWallet23dExecutionAuditExport.v1",
        "mcelExecutionAuditExport.v1",
        "mcelExecutionProofPacket.v1",
        "mcelExecutionProofHashChain.v1",
        "mcelExecutionAuditRedactionPolicy.v1",
        "mcelExecutionAuditExportReceipt.v1",
        'unlockVersion: "23D-MCEL"',
        "execution-audit-export-proof-packet",
        "Copy execution audit packet",
        "auditPacketPreview",
        "auditPacketHash",
        "proofPacketHash",
        "redactedByDefault: true",
        "fullDetailRequiresExplicitExport: true",
    ]
    for marker in markers:
        assert marker in lab

    redaction_modes = [
        '"full"',
        '"redacted"',
        '"hash-only"',
        'defaultMode: "redacted"',
    ]
    for marker in redaction_modes:
        assert marker in lab

    lifecycle_dependencies = [
        "wallet21aPolicyBoundSendGate",
        "wallet21bProviderOutcomeLedger",
        "wallet21cTransactionWatcher",
        "wallet21dRetryRecoverySafety",
        "wallet21ePostConfirmationMcelReceiptIntegration",
        "wallet21fRelockResetLifecycle",
        "wallet22aNetworkExecutionPolicyDecision",
        "wallet22cTargetRegistryContractBinding",
        "wallet22dProductionPolicyHardening",
        "wallet22ePolicyActivationRevocationLifecycle",
        "wallet22fPolicyBoundExecutionReadinessSurface",
        "wallet23aLiveNetworkExecutionPolish",
        "wallet23bTransactionObserver",
        "wallet23cSafeRecoveryPlanner",
        "transactionFinalityState",
        "recoveryPlanState",
        "includes23bFinalityState: true",
        "includes23cRecoveryPlan: true",
    ]
    for marker in lifecycle_dependencies:
        assert marker in lab

    assert "wallet_switchEthereumChain" not in lab
    assert "wallet_addEthereumChain" not in lab
    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert "broadcastTransaction" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)


def test_23d_is_bound_into_lab_code_studio_and_introduces_no_provider_mutation() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    app = read_app("mcel-lab.html")

    boundary_markers = [
        "boundary.wallet23dExecutionAuditExport = mcelWallet23dExecutionAuditExport(boundary",
        "boundary.walletExecutionAuditExport = boundary.wallet23dExecutionAuditExport",
        "wallet23dExecutionAuditExport: null",
        "walletExecutionAuditExport: null",
        "instance.runtime.wallet23dExecutionAuditExport = boundary.wallet23dExecutionAuditExport || null",
        "instance.runtime.walletExecutionAuditExport = boundary.walletExecutionAuditExport || boundary.wallet23dExecutionAuditExport || null",
        '"runtime.wallet23dExecutionAuditExport"',
        '"runtime.walletExecutionAuditExport"',
    ]
    for marker in boundary_markers:
        assert marker in lab

    render_markers = [
        'const executionAuditExportSlot = document.querySelector("#mcel-23d-wallet-execution-audit-export")',
        'const visible23dAuditExportSlot = document.querySelector("#mcel-23d-wallet-audit-export-visible-status")',
        "mcel-23d-wallet-execution-audit-export-view",
        "wallet23dExecutionAuditExport",
        "executionAuditExport",
        "executionProofPacket",
        "executionProofHashChain",
        "executionAuditRedactionPolicy",
        "executionAuditExportReceipt",
    ]
    for marker in render_markers:
        assert marker in lab

    html_markers = [
        "23D audit export",
        "23D execution audit export",
        'id="mcel-23d-wallet-audit-export-visible-status"',
        'id="mcel-23d-wallet-execution-audit-export"',
    ]
    for marker in html_markers:
        assert marker in app

    studio_markers = [
        "wallet23dExecutionAuditExport",
        "wallet23dExecutionAuditExportStatus",
        "wallet23dAuditPacketHash",
        "wallet23dRedactionMode",
        "executionAuditExportActive",
    ]
    for marker in studio_markers:
        assert marker in studio

    audit_defs = lab[
        lab.index("function mcelWallet23dExecutionAuditExport") : lab.index("function mcelWalletToolCommitBoundary")
    ]
    assert "eth_sendTransaction" not in audit_defs
    assert "eth_getTransactionReceipt" not in audit_defs
    assert "eth_blockNumber" not in audit_defs
    assert "eth_getTransactionByHash" not in audit_defs
    assert "providerMutationRequested: false" in audit_defs
    assert "mutationExecuted: false" in audit_defs

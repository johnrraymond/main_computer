from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "main_computer" / "web" / "applications" / "apps"
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
STYLES = ROOT / "main_computer" / "web" / "applications" / "styles"


def read_script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def read_app(name: str) -> str:
    return (APPS / name).read_text(encoding="utf-8")


def read_style(name: str) -> str:
    return (STYLES / name).read_text(encoding="utf-8")


def test_pre_18n_checkpoint_keeps_lab_and_studio_send_sign_locked() -> None:
    """The pre-18N checkpoint may inspect readiness, but must not execute txs."""

    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    assert "mcel-tx-draft-endgame-preflight.v1" in lab
    assert "send-sign-not-implemented" in lab

    for source in (lab, studio):
        assert "canSend" in source
        assert "canSign" in source
        assert "canBroadcast" in source
        assert "txDraftEndgamePreflight" in source

    lab_only_dangerous_methods = [
        "eth_sendTransaction",
        "eth_signTransaction",
        "personal_sign",
        "signTypedData",
        "sendTransaction",
        "broadcastTransaction",
    ]
    for method in lab_only_dangerous_methods:
        assert method not in lab
        assert method not in studio

    dangerous_call_patterns = [
        r"\.request\s*\(\s*\{\s*method\s*:\s*['\"]eth_sendTransaction['\"]",
        r"\.request\s*\(\s*\{\s*method\s*:\s*['\"]eth_signTransaction['\"]",
        r"\.request\s*\(\s*\{\s*method\s*:\s*['\"]personal_sign['\"]",
        r"\.sendTransaction\s*\(",
        r"\.signTypedData\s*\(",
    ]
    for pattern in dangerous_call_patterns:
        assert not re.search(pattern, lab)
        assert not re.search(pattern, studio)


def test_lab_pre_18n_checkpoint_requires_provenance_consumer_gate_and_no_send() -> None:
    lab = read_script("mcel-lab.js")

    expected_markers = [
        "mcelTinyContractTxDraftProvenance",
        "mcelTinyContractEnforceTxDraftProvenance",
        "mcelTinyContractTxDraftConsumerGate",
        "mcelTinyContractTxDraftEndgamePreflight",
        "txDraft.provenance.v1",
        "mcel-tx-draft-provenance-freshness.v1",
        "mcel-tx-draft-consumer-gate.v1",
        "mcel-tx-draft-endgame-preflight.v1",
        "futureBoundaryEligible",
        "provenanceEnforced",
        "noSendBoundaryPreserved",
        "send/sign preflight",
    ]
    for marker in expected_markers:
        assert marker in lab

    assert re.search(r"canSend\s*:\s*false", lab)
    assert re.search(r"canSign\s*:\s*false", lab)
    assert re.search(r"canBroadcast\s*:\s*false", lab)


def test_code_studio_pre_18n_checkpoint_displays_receipt_gate_not_wallet_execution() -> None:
    studio = read_script("code-editor-mcel-studio.js")

    expected_markers = [
        "Send/sign preflight",
        "Tx draft consumer gate",
        "Tx draft provenance",
        "Receipt Vector",
        "replayExpectationFailures",
        "txDraftEndgamePreflight",
        "txDraftConsumerGate",
        "canSend=",
        "canSign=",
        "canBroadcast=",
    ]
    for marker in expected_markers:
        assert marker in studio

    assert "window.ethereum" not in studio
    assert "provider.request" not in studio
    assert "wallet.request" not in studio


def test_pre_18n_checkpoint_pages_still_expose_mount_and_proof_surfaces() -> None:
    lab_html = read_app("mcel-lab.html")
    studio_html = read_app("code-editor.html")

    lab_markers = [
        'id="mcel-lab-app"',
        'class="mcel-tiny-contract-test"',
        "Dev Network Release Console",
        "SCM receipt",
    ]
    for marker in lab_markers:
        assert marker in lab_html

    studio_markers = [
        'id="code-editor-app"',
        "MCEL Code Studio",
        "Bottom Proof Dock",
        "Receipt Vector",
        "Replay",
        "Draft Provenance",
    ]
    for marker in studio_markers:
        assert marker in studio_html


def test_18n_mcel_commit_boundary_scaffold_is_mcel_only_and_locked() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    lab_markers = [
        "mcel-18n-commit-boundary.v1",
        "mcelCommitDraft.v1",
        "mcelCommitProvenance.v1",
        "mcelCommitFreshness.v1",
        "mcelCommitConsumerGate.v1",
        "mcelCommitPreflight.v1",
        "mcelCommitReceipt.v1",
        "mcelWalletToolCommitBoundary.v1",
        "No serious MCEL action commits from raw UI state.",
        "walletCommitBoundary",
        "wallet-send-sign-locked",
        "mutationExecuted: false",
    ]
    for marker in lab_markers:
        assert marker in lab

    studio_markers = [
        "normalizeMcelCommitBoundaryForReceipt",
        "summarizeMcelCommitBoundaryForWorkbench",
        "mcel-code-studio-18n-commit-boundary-summary",
        "mcel-code-studio-18n-commit-boundary-workbench-summary",
        "MCEL 18N boundary",
        "MCEL 18N receipt",
    ]
    for marker in studio_markers:
        assert marker in studio

    for source in (lab, studio):
        assert re.search(r"canSend\s*:\s*false", source) or "canSend=${" in source
        assert re.search(r"canSign\s*:\s*false", source) or "canSign=${" in source
        assert re.search(r"canBroadcast\s*:\s*false", source) or "canBroadcast=${" in source


def test_18n_wallet_tool_mount_exposes_preflight_and_receipt_without_unlocking_wallet() -> None:
    lab_html = read_app("mcel-lab.html")
    studio_html = read_app("code-editor.html")
    lab = read_script("mcel-lab.js")

    lab_html_markers = [
        'data-mcel-18n-wallet-tool="true"',
        'id="mcel-18n-wallet-tool-status"',
        'id="mcel-18n-wallet-tool-preflight"',
        'id="mcel-18n-wallet-tool-receipt"',
        'id="mcel-18n-wallet-tool-ledger"',
        'id="mcel-18n-wallet-tool-refresh"',
        'id="mcel-18n-wallet-tool-copy-receipt"',
        "18N wallet commit boundary",
        "Check wallet + 18N preflight",
        "Send/sign/broadcast remains locked",
    ]
    for marker in lab_html_markers:
        assert marker in lab_html

    studio_html_markers = [
        "18N Commit Boundary",
        'data-mcel-18n-commit-boundary-mount="receipt-vector"',
    ]
    for marker in studio_html_markers:
        assert marker in studio_html

    assert "renderMcel18nWalletToolSurface" in lab
    assert "refreshMcel18nWalletToolBoundary" in lab
    assert "copyMcel18nWalletToolReceipt" in lab
    assert "mcel-18n-wallet-tool-preflight-view" in lab
    assert "mcel-18n-wallet-tool-receipt-view" in lab
    assert "mcel-18n-wallet-tool-receipt-ledger-view" in lab
    assert "receiptLedger" in lab
    assert "wallet unlock requirements are incomplete in 18N-MCEL-j" in lab

def test_18n_mcel_code_studio_commit_boundary_guards_runtime_commit_and_persistence() -> None:
    studio = read_script("code-editor-mcel-studio.js")
    studio_html = read_app("code-editor.html")

    markers = [
        'MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION = "18N-MCEL-j"',
        "mcelCodeStudioCommitBoundary.v1",
        "buildMcelCodeStudioCommitBoundary",
        "mcelCodeStudioCommitDraft",
        "mcelCodeStudioCommitFreshness",
        "mcelCodeStudioCommitConsumerGate",
        "mcelCodeStudioCommitPreflight",
        "mcelCodeStudioCommitReceipt",
        "recordMcelCodeStudioCommitBoundary(commitBoundary)",
        "renderMcelCodeStudioCommitBoundaryInProofDock",
        "codeStudio.commitRuntimeDraft",
        "codeStudio.persistLiveWorkspace",
        "consumer gate must run before source/localStorage mutation",
        "runtime draft intent is explicit before source mutation",
        "commitBoundaryReceipt: context.commitBoundaryReceipt",
        "mutationExecuted: true",
    ]
    for marker in markers:
        assert marker in studio

    assert studio.index("buildMcelCodeStudioCommitBoundary({\n          action: \"codeStudio.commitRuntimeDraft\"") < studio.index("target.textContent = draft.value")
    assert studio.index("persistenceBoundary = buildMcelCodeStudioCommitBoundary") < studio.index("storage.setItem(LIVE_WORKSPACE_PERSISTENCE_KEY")
    assert "canSend: false" in studio
    assert "canSign: false" in studio
    assert "canBroadcast: false" in studio

    html_markers = [
        'id="code-studio-18n-commit-boundary-status"',
        'data-mcel-18n-code-studio-boundary="runtime-commit"',
        'id="code-studio-show-18n-boundary"',
        "Show 18N boundary",
    ]
    for marker in html_markers:
        assert marker in studio_html



def test_18n_mcel_code_studio_runtime_mount_uses_boundary_before_generated_chrome() -> None:
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "codeStudio.mountRuntimeDraft",
        "runtime-mount-preflight",
        "mountRuntimeDraft-with-receipt",
        "mountMonaco-runtime-only",
        "Runtime mount blocked by MCEL 18N boundary",
        "source-workspace-missing-or-invalid",
        "selected-file-missing",
        "runtime-chrome-in-source",
        "mount-runtime-draft-runtime-only-mutation",
        "Code Studio runtime mount is now an 18N boundary specimen",
        "Code Studio cannot mount runtime chrome without a fresh source snapshot",
    ]
    for marker in markers:
        assert marker in studio

    render_runtime_start = studio.index("function renderRuntime()")
    mount_boundary_index = studio.index("buildMcelCodeStudioCommitBoundary({\n          action: \"codeStudio.mountRuntimeDraft\"", render_runtime_start)
    runtime_preview_write = studio.index("runtimePreview.innerHTML = `", render_runtime_start)
    runtime_monaco_mount = studio.index("mountRuntimeMonaco(file, draft)", render_runtime_start)
    assert mount_boundary_index < runtime_preview_write
    assert studio.index("recordMcelCodeStudioCommitBoundary(mountBoundary)", render_runtime_start) < runtime_monaco_mount
    assert "mutationExecuted: true" in studio
    assert "canSend: false" in studio
    assert "canSign: false" in studio
    assert "canBroadcast: false" in studio


def test_18n_code_studio_boundary_status_stays_in_runtime_toolbar() -> None:
    studio_html = read_app("code-editor.html")

    runtime_toolbar = re.search(
        r'<section class="code-studio-editor-pane"[^>]*data-code-studio-pane="runtime"'
        r'.*?<div class="code-studio-pane-toolbar">(.*?)</div>\s*'
        r'<div class="code-studio-runtime-preview"',
        studio_html,
        re.S,
    )
    assert runtime_toolbar, "runtime pane should keep a toolbar followed directly by the runtime preview"
    assert 'id="code-studio-18n-commit-boundary-status"' in runtime_toolbar.group(1)
    assert '</div>\n          <output id="code-studio-18n-commit-boundary-status"' not in studio_html


def test_18n_mcel_wallet_txdraft_first_class_stale_rebuild_and_receipt_wall() -> None:
    lab = read_script("mcel-lab.js")
    lab_html = read_app("mcel-lab.html")

    markers = [
        "mcelWalletTxDraft.v1",
        "mcelWalletTxProvenance.v1",
        "mcelWalletFreshnessSnapshot.v1",
        "mcelWalletPreflightReport.v1",
        "mcelWalletBlockedAttemptReceipt.v1",
        "mcelWalletRebuildDraftAction.v1",
        "mcelWalletStaleDraftSimulation.v1",
        "walletTxDraft",
        "walletTxProvenance",
        "walletFreshnessSnapshot",
        "walletPreflightReport",
        "walletBlockedAttemptReceipt",
        "walletRebuildDraftAction",
        "account-changed-since-draft",
        "chain-changed-since-draft",
        "source-request-changed-since-draft",
        "target-or-value-changed-since-draft",
        "draft-not-explicitly-reviewed",
        "rebuild draft from current wallet state",
        "Refresh preflight does not silently make stale draft usable",
        "blocked wallet attempts produce receipts",
        "mutationExecuted: false",
        "canSend: false",
        "canSign: false",
        "canBroadcast: false",
        "wallet unlock requirements are incomplete in 18N-MCEL-j",
    ]
    for marker in markers:
        assert marker in lab

    html_markers = [
        "Rebuild draft from current wallet state",
        "Simulate account stale",
        "Simulate chain stale",
        "Simulate source request stale",
        "Simulate target/value stale",
        "MCEL wallet txDraft specimen is waiting",
        "MCEL wallet freshness snapshot is waiting",
    ]
    for marker in html_markers:
        assert marker in lab_html

    assert lab.index("mcelWalletTxDraftSpecimen") < lab.index("mcelWalletFreshnessSnapshot")
    assert lab.index("mcelWalletFreshnessSnapshot") < lab.index("mcelWalletBlockedAttemptReceipt")
    assert lab.index("mcelWalletBlockedAttemptReceipt") < lab.index("function mcelWalletToolCommitBoundary")


def test_18n_wallet_refresh_cannot_silently_repair_stale_draft() -> None:
    lab = read_script("mcel-lab.js")

    refresh_index = lab.index("function refreshMcel18nWalletToolBoundary")
    rebuild_index = lab.index("async function rebuildMcel18nWalletTxDraft")
    simulate_index = lab.index("function simulateMcel18nWalletStaleDraft")

    assert "simulation: tinyState.walletStaleSimulation || null" in lab[refresh_index:refresh_index + 900]
    assert "tinyState.walletStaleSimulation = null" in lab[rebuild_index:rebuild_index + 900]
    assert "refresh preflight only reports stale intent" in lab
    assert "rebuild-wallet-tx-draft-receipt" in lab
    assert "simulate-wallet-account-change" in lab
    assert "simulate-wallet-chain-change" in lab
    assert "simulate-wallet-source-request-change" in lab
    assert "simulate-wallet-target-value-change" in lab
    assert simulate_index < rebuild_index
    assert rebuild_index < lab.index("async function copyMcel18nWalletToolReceipt")



def test_18n_mcel_proof_dock_unifies_wallet_and_code_studio_commit_specimens() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    studio_html = read_app("code-editor.html")

    studio_markers = [
        'MCEL_PROOF_DOCK_UNIFICATION_VERSION = "18N-MCEL-j"',
        "mcelProofDockUnifiedSpecimens.v1",
        "mcelProofDockCommitBoundarySpecimen.v1",
        "collectMcelProofDockUnifiedSpecimens",
        "renderMcelUnifiedProofDockSpecimensInProofDock",
        "codeStudio.runtimeMount",
        "codeStudio.editorDraftCommit",
        "codeStudio.workspacePersist",
        "wallet.txDraft",
        "wallet.blockedSend",
        "wallet.blockedSign",
        "wallet.blockedBroadcast",
        "Open 18N specimens in proof dock",
        "copy-mcel-proof-dock-unified-specimens",
        "sourceWalletProofDockSpecimens",
        "Unified MCEL 18N proof dock specimens",
    ]
    for marker in studio_markers:
        assert marker in studio

    lab_markers = [
        "mcelWalletProofDockSpecimens",
        "mcelProofDockCommitBoundarySpecimen",
        "mcelProofDockUnifiedSpecimens.v1",
        "wallet.blockedSend",
        "wallet.blockedSign",
        "wallet.blockedBroadcast",
        "mcelProofDockSpecimens",
        "proofDockSpecimens",
        "18N proof dock specimens",
        "Proof dock treats wallet and Code Studio commit boundaries as specimens of the same 18N family.",
    ]
    for marker in lab_markers:
        assert marker in lab

    assert 'data-mcel-18n-commit-boundary-mount="receipt-vector"' in studio_html
    assert studio.index("collectMcelProofDockUnifiedSpecimens") < studio.index("renderMcelUnifiedProofDockSpecimensInProofDock")
    assert lab.index("mcelWalletProofDockSpecimens") < lab.index("function mcelWalletToolCommitBoundary")
    assert "window.ethereum" not in studio
    assert "provider.request" not in studio
    assert "wallet.request" not in studio
    assert re.search(r"canSend\s*:\s*false", lab)
    assert re.search(r"canSign\s*:\s*false", lab)
    assert re.search(r"canBroadcast\s*:\s*false", lab)


def test_18n_mcel_h_i_j_wallet_negative_paths_unlock_requirements_and_final_locked_specimen() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    lab_html = read_app("mcel-lab.html")

    lab_markers = [
        "mcelWalletNegativePathTestWall.v1",
        "mcelWalletUnlockRequirements.v1",
        "mcelWalletFinalLockedSpecimen.v1",
        "negative-path tests prove no wallet mutation path unlocks by accident",
        "stale wallet draft blocks",
        "missing provenance blocks",
        "missing preflight blocks",
        "locked consumer gate blocks",
        "blocked attempts produce receipts",
        "Unlock requirements: incomplete",
        "required account match",
        "required chain match",
        "required draft hash match",
        "required source request match",
        "required preflight pass",
        "required consumer gate pass",
        "required explicit user confirmation",
        "required receipt emission",
        "required provider unlock implementation",
        "stop here until a separate wallet unlock design is blessed",
        "consumer gate refuses before provider execution",
        "wallet unlock requirements are incomplete in 18N-MCEL-j",
        "final locked wallet specimen refuses before provider execution",
        "walletNegativePathTestWall",
        "walletUnlockRequirements",
        "walletFinalLockedSpecimen",
    ]
    for marker in lab_markers:
        assert marker in lab

    html_markers = [
        'id="mcel-18n-wallet-tool-negative-paths"',
        'id="mcel-18n-wallet-tool-unlock-requirements"',
        'id="mcel-18n-wallet-tool-final-locked-specimen"',
        "MCEL 18N wallet negative-path test wall is waiting",
        "Unlock requirements: incomplete",
        "Final locked wallet specimen is waiting",
    ]
    for marker in html_markers:
        assert marker in lab_html

    studio_markers = [
        "walletUnlockStatus",
        "walletFinalLockedSpecimenStatus",
        "Wallet unlock requirements remain incomplete until a separate explicit unlock design patch.",
        "walletUnlockRequirements",
        "walletFinalLockedSpecimen",
    ]
    for marker in studio_markers:
        assert marker in studio

    assert lab.index("function mcelWalletNegativePathTestWall") < lab.index("function mcelWalletUnlockRequirements")
    assert lab.index("function mcelWalletUnlockRequirements") < lab.index("function mcelWalletFinalLockedSpecimen")
    assert lab.index("boundary.walletNegativePathTestWall = mcelWalletNegativePathTestWall(boundary)") < lab.index("boundary.mcelProofDockSpecimens = mcelWalletProofDockSpecimens(boundary)")
    assert re.search(r"readyForProviderExecution\s*:\s*false", lab)
    assert re.search(r"canSend\s*:\s*false", lab)
    assert re.search(r"canSign\s*:\s*false", lab)
    assert re.search(r"canBroadcast\s*:\s*false", lab)


def test_18n_mcel_h_negative_wall_still_has_no_wallet_mutation_rpc_paths() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

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

    dangerous_call_patterns = [
        r"\.request\s*\(\s*\{\s*method\s*:\s*['\"]eth_sendTransaction['\"]",
        r"\.request\s*\(\s*\{\s*method\s*:\s*['\"]eth_signTransaction['\"]",
        r"\.request\s*\(\s*\{\s*method\s*:\s*['\"]personal_sign['\"]",
        r"\.sendTransaction\s*\(",
        r"\.signTypedData\s*\(",
    ]
    for pattern in dangerous_call_patterns:
        assert not re.search(pattern, lab)
        assert not re.search(pattern, studio)

    assert "attempt send/sign/broadcast" in lab
    assert "refused-before-provider" in lab
    assert "mutationExecuted: false" in lab



def test_18n_mcel_j_visible_wallet_specimen_board_makes_locked_state_obvious() -> None:
    lab_html = read_app("mcel-lab.html")
    lab = read_script("mcel-lab.js")
    lab_css = read_style("mcel-lab.css")

    html_markers = [
        'data-mcel-18n-wallet-visible-board="true"',
        'id="mcel-18n-wallet-tool-visible-phase"',
        'id="mcel-18n-wallet-tool-visible-tx-draft-status"',
        'id="mcel-18n-wallet-tool-visible-freshness-status"',
        'id="mcel-18n-wallet-tool-visible-unlock-status"',
        'id="mcel-18n-wallet-tool-visible-provider-status"',
        'id="mcel-18n-wallet-tool-visible-flow"',
        'id="mcel-18n-wallet-tool-visible-blockers"',
        'id="mcel-18n-wallet-tool-visible-next"',
        "18N-J final locked wallet specimen",
        "Provider mutation",
        "refused before provider",
    ]
    for marker in html_markers:
        assert marker in lab_html

    script_markers = [
        "visiblePhaseSlot",
        "visibleProviderStatusSlot",
        "visibleFlowSlot",
        "visibleBlockers",
        "walletFinalLockedSpecimen.flow",
        "refused-before-provider",
        "18N-MCEL-j visible",
        "stop here until a separate wallet unlock design is blessed",
    ]
    for marker in script_markers:
        assert marker in lab

    css_markers = [
        ".mcel-18n-wallet-visible-board",
        ".mcel-18n-wallet-kpis",
        ".mcel-18n-wallet-flow",
        "#mcel-18n-wallet-tool-visible-blockers",
    ]
    for marker in css_markers:
        assert marker in lab_css

    assert lab.index('document.querySelector("#mcel-18n-wallet-tool-visible-provider-status")') < lab.index("const visibleBlockers =")
    assert re.search(r"canSend\s*:\s*false", lab)
    assert re.search(r"canSign\s*:\s*false", lab)
    assert re.search(r"canBroadcast\s*:\s*false", lab)



def test_18n_mcel_wallet_declares_selectable_backlog_remediation_schemes() -> None:
    lab = read_script("mcel-lab.js")
    lab_html = read_app("mcel-lab.html")
    lab_css = read_style("mcel-lab.css")

    scheme_ids = [
        "ignore",
        "disable-while-busy",
        "single-flight",
        "drop-while-busy",
        "queue-serial",
        "latest-wins",
        "coalesce",
        "debounce",
        "throttle",
        "cooldown",
        "block-before-external-provider",
    ]

    for scheme_id in scheme_ids:
        assert f'value="{scheme_id}"' in lab_html
        assert scheme_id in lab

    html_markers = [
        'data-mcel-wallet-backlog-remediation="true"',
        'name="mcel-wallet-backlog-remediation-scheme"',
        'id="mcel-18n-wallet-backlog-scheme-status"',
        "Wallet backlog remediation scheme",
        "Latest wins / nuke backlog",
        "SCM receipts the selected policy instead of guessing component intent.",
    ]
    for marker in html_markers:
        assert marker in lab_html

    script_markers = [
        "MCEL_WALLET_BACKLOG_REMEDIATION_SCHEMES",
        "mcelWalletComponentBacklogRemediationPolicy.v1",
        "mcelWalletBacklogRemediationReceipt.v1",
        "mcelWalletBacklogRuntimeSnapshot.v1",
        "runMcelWalletBacklogRemediated",
        "setMcelWalletBacklogRemediationScheme",
        "renderMcelWalletBacklogSchemeControls",
        "mcelWalletBacklogExecutionOptionsForScheme",
        "mcelWalletProofRenderPolicy.v1",
        "guardPermissionRequest",
        "scheduleMcelWalletBacklogProofRender",
        "SCM does not infer wallet backlog semantics",
        "wallet component owns its backlog policy",
        "latest-wins-queued",
        "nukedBacklog",
        "provider request was not re-issued",
    ]
    for marker in script_markers:
        assert marker in lab

    css_markers = [
        ".mcel-wallet-backlog-schemes",
        ".mcel-wallet-backlog-scheme-grid",
        "#mcel-18n-wallet-backlog-scheme-status",
    ]
    for marker in css_markers:
        assert marker in lab_css

    connect_index = lab.index('async function connectMcelTinyContractWallet')
    disconnect_index = lab.index('async function disconnectMcelTinyContractWallet')
    perform_connect_index = lab.index('async function performMcelTinyContractWalletConnect')
    perform_disconnect_index = lab.index('async function performMcelTinyContractWalletDisconnect')

    assert lab.index('runMcelWalletBacklogRemediated("wallet.connect"', connect_index) < perform_connect_index
    assert lab.index('runMcelWalletBacklogRemediated("wallet.disconnect"', disconnect_index) < perform_disconnect_index
    latest_index = lab.index('if (scheme.id === "latest-wins" && runtime.active)')
    assert lab.index('runtime.nukedCount', latest_index) > latest_index
    queue_index = lab.index('if (scheme.id === "queue-serial" && runtime.active)')
    assert lab.index('runtime.queued.push', queue_index) > queue_index
    assert lab.index('method === "eth_requestAccounts"') < lab.index('provider.request({method, params})')
    assert lab.index('provider.events.reused') < lab.index('provider.on("accountsChanged"')

    request_index = lab.index("async function mcelTinyContractWalletRequest")
    request_end = lab.index("function runMcelTinyContractProviderEventEffect", request_index)
    request_body = lab[request_index:request_end]
    assert "selectedMcelWalletBacklogRemediationScheme" not in request_body
    assert "requestPolicy?.guardPermissionRequest === true" in request_body

    proof_render_index = lab.index("function scheduleMcelWalletBacklogProofRender")
    proof_render_end = lab.index("function mcelWalletProviderPendingBackoffActive", proof_render_index)
    proof_render_body = lab[proof_render_index:proof_render_end]
    assert "selectedMcelWalletBacklogRemediationScheme" not in proof_render_body
    assert 'policy.strategy === "immediate"' in proof_render_body
    assert "walletBacklogOptions.proofRender" in lab



def test_18n_mcel_wallet_backlog_controls_do_not_sticky_gray_buttons() -> None:
    lab = read_script("mcel-lab.js")

    assert "mcelWalletBacklogButtonDisabled" in lab
    assert 'if (scheme?.id === "disable-while-busy") return true;' in lab
    assert 'if (scheme?.id === "cooldown") return action === runtime.action;' in lab
    assert 'scheme.id === "block-before-external-provider" && action === "wallet.connect"' in lab
    assert 'if (scheme.id === "cooldown") {' in lab
    assert 'scheme.id === "cooldown" || scheme.id === "disable-while-busy" || scheme.id === "block-before-external-provider"' not in lab
    assert 'status: "already-satisfied"' in lab
    assert "mcelTinyContractConnectedWalletSnapshot" in lab
    assert "beginMcelWalletProviderPermissionRequest" in lab
    assert "clearMcelWalletProviderPermissionRequest" in lab


def test_18n_mcel_wallet_lifecycle_governance_is_action_specific() -> None:
    lab = read_script("mcel-lab.js")

    required_markers = [
        "walletLifecycleCommonRequiredChecks",
        "walletLifecycleConnectPassRequiredChecks",
        "walletLifecycleConnectBlockedRequiredChecks",
        "walletLifecycleDisconnectRequiredChecks",
        "walletConnectProviderEvidenceCaptured",
        "walletPermissionRevokeEvidenceCaptured",
        "externalOutcomeRpcMethods",
        "walletProofRpcMethods",
    ]
    for marker in required_markers:
        assert marker in lab

    common_index = lab.index("const walletLifecycleCommonRequiredChecks")
    common_end = lab.index("const walletLifecycleConnectPassRequiredChecks", common_index)
    common_body = lab[common_index:common_end]
    assert "checks.txDraftBlockedAfterExternalOutcome" in common_body
    assert "checks.sourceSafeAfterExternalOutcome" in common_body

    blocked_index = lab.index("const walletLifecycleConnectBlockedRequiredChecks")
    blocked_end = lab.index("const walletLifecycleDisconnectRequiredChecks", blocked_index)
    blocked_body = lab[blocked_index:blocked_end]
    assert "checks.walletConnectOutcomeCaptured" in blocked_body
    assert "checks.walletConnectProviderEvidenceCaptured" in blocked_body
    assert "checks.walletEffectRan" not in blocked_body
    assert "checks.networkVerified" not in blocked_body

    disconnect_index = lab.index("const walletLifecycleDisconnectRequiredChecks")
    disconnect_end = lab.index("const walletLifecycleRequiredChecks", disconnect_index)
    disconnect_body = lab[disconnect_index:disconnect_end]
    assert "checks.walletDisconnectOutcomeCaptured" in disconnect_body
    assert "checks.walletDisconnectReset" in disconnect_body
    assert "checks.walletPermissionRevokeAttempted" in disconnect_body
    assert "checks.walletPermissionRevokeEvidenceCaptured" in disconnect_body
    assert "checks.walletAdapterExercised" not in disconnect_body
    assert "checks.networkVerified" not in disconnect_body
    assert "checks.walletEffectRan" not in disconnect_body

    pass_index = lab.index("const walletLifecycleConnectPassRequiredChecks")
    pass_end = lab.index("const walletLifecycleConnectBlockedRequiredChecks", pass_index)
    pass_body = lab[pass_index:pass_end]
    assert "checks.walletEffectRan" in pass_body
    assert "checks.walletAdapterExercised" in pass_body
    assert "checks.networkVerified" in pass_body

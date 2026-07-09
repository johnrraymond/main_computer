from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"


def read_script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def function_body(source: str, name: str) -> str:
    marker = f"function {name}"
    start = source.index(marker)
    next_fn = source.find("\n    function ", start + len(marker))
    if next_fn == -1:
        return source[start:]
    return source[start:next_fn]


def test_passive_provider_initial_snapshot_is_an_explicit_scm_contract() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "wallet.provider.initialSnapshot",
        "mcelWalletProviderInitialSnapshot.v1",
        "mount-passive-provider-snapshot",
        "passive-provider-snapshot",
        "authorized-account-observed",
        "no-authorized-account",
        "runtime.walletProviderInitialSnapshot",
        "walletProviderInitialSnapshotCaptured",
        "walletProviderInitialSnapshotEffectRan",
        "Passive provider snapshot hydrated runtime wallet state through a declared SCM effect.",
        "buttonRenderSource: \"runtime.wallet\"",
        "permissionRequested: false",
        "providerMutationRequested: false",
        "providerMutationExecuted: false",
        "readMcelTinyContractWalletInitialProviderSnapshot",
        "hydrateMcelTinyContractWalletInitialSnapshot",
    ]
    for marker in markers:
        assert marker in lab

    studio_markers = [
        "wallet.provider.initialSnapshot",
        "provider-observation",
        "render connected wallet from SCM runtime",
        "SCM passive provider snapshot contract keeps wallet buttons downstream of runtime.wallet",
    ]
    for marker in studio_markers:
        assert marker in studio



def test_initial_snapshot_effect_manifest_uses_only_valid_scm_paths() -> None:
    lab = read_script("mcel-lab.js")

    effect_header = '"wallet.provider.initialSnapshot": {'
    effect_start = lab.index(effect_header)
    next_effect = lab.index('\n          "wallet.provider.accountsChanged"', effect_start)
    effect_contract = lab[effect_start:next_effect]

    assert '"mount.lifecycle"' not in effect_contract
    assert 'triggers: ["runtime.walletAdapter"]' in effect_contract
    assert 'reads: ["source.devRelease.devNetwork", "runtime.wallet", "runtime.network", "runtime.walletEvents"]' in effect_contract
    assert 'writes: ["runtime.wallet", "runtime.network", "runtime.walletAdapter", "runtime.walletProviderInitialSnapshot", "runtime.walletEvents", "runtime.externalOutcome", "runtime.evidenceStrip"]' in effect_contract


def test_initial_snapshot_uses_read_only_authorization_observation_not_permission_or_chain_mutation() -> None:
    lab = read_script("mcel-lab.js")
    read_snapshot = function_body(lab, "readMcelTinyContractWalletInitialProviderSnapshot")
    hydrate_snapshot = function_body(lab, "hydrateMcelTinyContractWalletInitialSnapshot")

    assert '"eth_accounts"' in read_snapshot
    assert '"eth_chainId"' in read_snapshot
    assert "eth_requestAccounts" not in read_snapshot
    assert "eth_requestAccounts" not in hydrate_snapshot
    assert "ensureExpectedChain" not in read_snapshot
    assert "ensureExpectedChain" not in hydrate_snapshot
    assert "requestConnect" not in read_snapshot
    assert "requestConnect" not in hydrate_snapshot
    assert "wallet_switchEthereumChain" not in lab
    assert "wallet_addEthereumChain" not in lab
    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert "broadcastTransaction" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)


def test_initial_snapshot_commits_scm_state_before_button_render_state_can_change() -> None:
    lab = read_script("mcel-lab.js")

    mount_call = 'void hydrateMcelTinyContractWalletInitialSnapshot(app, runtime.instance, "mount-passive-provider-snapshot");'
    assert mount_call in lab

    effect_order = [
        'window.McelLabScm.runEffect(instance, "wallet.provider.initialSnapshot", payload)',
        'refreshMcel18nWalletToolBoundary(`${reason}-boundary-refresh`)',
        'syncMcelTinyContractDomFromScm(target, instance, `${reason}-committed`)',
        'scheduleMcelWalletBacklogProofRender(target, `${reason}-proof-render`',
    ]
    positions = [lab.index(marker) for marker in effect_order]
    assert positions == sorted(positions)

    button_snapshot = function_body(lab, "mcelTinyContractConnectedWalletSnapshot")
    golden_button_sync = function_body(lab, "syncMcelGoldenWalletControl")

    assert "const wallet = instance?.runtime?.wallet" in button_snapshot
    assert "snapshotMcelTinyContractWalletSubsystemState" not in button_snapshot
    assert "wallet-subsystem" not in button_snapshot
    assert "providerSnapshot" not in button_snapshot
    assert "eth_accounts" not in button_snapshot
    assert "selectedAddress" not in button_snapshot
    assert "ethereum" not in golden_button_sync
    assert "eth_accounts" not in golden_button_sync
    assert "selectedAddress" not in golden_button_sync


def test_initial_snapshot_retries_provider_settle_without_promoting_button_to_provider_reader() -> None:
    lab = read_script("mcel-lab.js")

    assert "mcelWalletProviderInitialSnapshotRetry.v1" in lab
    assert "scheduleMcelTinyContractWalletInitialSnapshotRetry" in lab
    assert "provider-settle-retry" in lab
    assert "clearMcelTinyContractWalletInitialSnapshotRetry" in lab
    assert "tinyState.providerInitialSnapshotRetry = null" in lab

    retry_body = function_body(lab, "scheduleMcelTinyContractWalletInitialSnapshotRetry")
    assert "window.setTimeout" in retry_body
    assert "hydrateMcelTinyContractWalletInitialSnapshot(" in retry_body
    assert "eth_requestAccounts" not in retry_body
    assert "requestConnect" not in retry_body
    assert "wallet_switchEthereumChain" not in retry_body
    assert "wallet_addEthereumChain" not in retry_body


def test_interactive_connect_reconciles_passive_authorized_accounts_before_requesting_permission() -> None:
    lab = read_script("mcel-lab.js")
    connect_body = function_body(lab, "connectMcelTinyContractWallet")

    passive_call = 'await hydrateMcelTinyContractWalletInitialSnapshot(app, instance, `${reason}-preconnect-passive-snapshot`, {retry: false})'
    interactive_call = 'runMcelWalletBacklogRemediated("wallet.connect"'
    assert passive_call in connect_body
    assert interactive_call in connect_body
    assert connect_body.index(passive_call) < connect_body.index(interactive_call)
    assert "eth_requestAccounts" not in connect_body
    assert "providerSnapshot" not in connect_body
    assert "selectedAddress" not in connect_body


def test_wallet_lifecycle_receipt_treats_initial_snapshot_as_governed_wallet_lifecycle() -> None:
    lab = read_script("mcel-lab.js")

    assert "walletInitialSnapshotProviderEvidenceCaptured" in lab
    assert 'externalOutcomeOperation === "wallet.provider.initialSnapshot"' in lab
    assert "walletLifecycleInitialSnapshotRequiredChecks" in lab
    assert "checks.walletProviderInitialSnapshotEffectRan" in lab
    assert "checks.walletInitialSnapshotProviderEvidenceCaptured" in lab
    assert "tinyState.walletConnectCount > 0 || tinyState.walletAuthorizationPendingCount > 0 || tinyState.walletDisconnectPendingCount > 0 || tinyState.walletDisconnectCount > 0 || tinyState.providerInitialSnapshotCount > 0" in lab
    assert "initial provider snapshot:" in lab


def test_interactive_wallet_authorization_pending_is_a_governed_lifecycle_state() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "wallet.connect.authorizationPending",
        "mcelWalletConnectAuthorizationPending.v1",
        "interactive-wallet-authorization",
        "authorization-pending",
        "permission-request-pending",
        "Wallet connect requested account authorization and is waiting for the provider/user response.",
        "walletAuthorizationPendingCount",
        "walletConnectAuthorizationPendingEvidenceCaptured",
        "walletLifecycleConnectPendingRequiredChecks",
        "PENDING: wallet authorization is waiting on the provider; SCM containment passed",
        "wallet authorization pending:",
    ]
    for marker in markers:
        assert marker in lab

    pending_effect = lab[lab.index('"wallet.connect.authorizationPending": {'):lab.index('\n          "wallet.disconnect"', lab.index('"wallet.connect.authorizationPending": {'))]
    assert 'triggers: ["state.walletGate"]' in pending_effect
    assert 'writes: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletAuthorizationPending", "runtime.walletEvents", "runtime.walletAdapter", "runtime.externalOutcome", "runtime.evidenceStrip"]' in pending_effect
    assert 'status: "pending"' in pending_effect
    assert 'permissionRequested: true' in pending_effect
    assert 'providerMutationRequested: false' in pending_effect
    assert 'runtime.wallet.connected=false' in pending_effect

    perform_connect = function_body(lab, "performMcelTinyContractWalletConnect")
    pending_call = "commitMcelTinyContractWalletConnectAuthorizationPending(app, instance, `${reason}-authorization-pending`)"
    request_call = "readMcelTinyContractWalletProvider(true, walletBacklogOptions)"
    assert pending_call in perform_connect
    assert request_call in perform_connect
    assert perform_connect.index(pending_call) < perform_connect.index(request_call)

    helper = function_body(lab, "commitMcelTinyContractWalletConnectAuthorizationPending")
    assert 'window.McelLabScm.runEffect(instance, "wallet.connect.authorizationPending"' in helper
    assert 'renderMcelTinyContractProof(target, `${reason}-authorization-pending`)' in helper
    assert '"eth_accounts"' not in helper
    assert "wallet_switchEthereumChain" not in lab
    assert "wallet_addEthereumChain" not in lab
    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert "broadcastTransaction" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)

def test_interactive_authorization_pending_runtime_write_is_owned_by_scm_manifest() -> None:
    lab = read_script("mcel-lab.js")

    runtime_ownership = lab[lab.index("          runtime: ["):lab.index("          ],\n          layout:", lab.index("          runtime: ["))]
    pending_effect = lab[lab.index('"wallet.connect.authorizationPending": {'):lab.index('\n          "wallet.disconnect"', lab.index('"wallet.connect.authorizationPending": {'))]
    runtime_defaults = function_body(lab, "mcelTinyContractRuntimeDefaults")

    assert '"walletAuthorizationPending"' in runtime_ownership
    assert '"runtime.walletAuthorizationPending"' in pending_effect
    assert "walletAuthorizationPending: null" in runtime_defaults


def test_component_effect_writes_remain_inside_declared_runtime_ownership() -> None:
    lab = read_script("mcel-lab.js")

    manifest = lab[lab.index("function mcelTinyContractScmManifest"):lab.index("function mcelTinyContractRouteManifest")]
    runtime_ownership_block = manifest[manifest.index("          runtime: ["):manifest.index("          ],\n          layout:", manifest.index("          runtime: ["))]
    owned_runtime_paths = set(re.findall(r'"([A-Za-z0-9_$-]+)"', runtime_ownership_block))

    for path in re.findall(r'"runtime\.([A-Za-z0-9_$-]+)"', manifest):
        assert path in owned_runtime_paths, f"runtime.{path} is written/read by an effect but is not owned"


def test_wallet_lifecycle_receipt_is_not_failed_by_layout_only_transient() -> None:
    lab = read_script("mcel-lab.js")

    wallet_common = lab[lab.index("const walletLifecycleCommonRequiredChecks = ["):lab.index("const walletLifecycleInitialSnapshotRequiredChecks", lab.index("const walletLifecycleCommonRequiredChecks = ["))]
    wallet_reset = lab[lab.index("const walletResetOnlyRequiredChecks = ["):lab.index("const walletLifecycleCommonRequiredChecks = [", lab.index("const walletResetOnlyRequiredChecks = ["))]
    full_battery = lab[lab.index("const fullBatteryRequiredChecks = ["):lab.index("const receiptMode =", lab.index("const fullBatteryRequiredChecks = ["))]

    assert "checks.layoutContractChecked" not in wallet_common
    assert "checks.styleContractChecked" not in wallet_common
    assert "checks.layoutContractChecked" not in wallet_reset
    assert "checks.styleContractChecked" not in wallet_reset
    assert "checks.layoutContractChecked" in full_battery
    assert "checks.styleContractChecked" in full_battery

    assert "walletLifecycleSafetyRequiredChecks" in lab
    assert "fullBatterySafetyRequiredChecks" in lab
    assert 'externalOutcomeOperation === "wallet.provider.initialSnapshot" ? "not required for passive snapshot"' in lab
    assert "layout/style checked:" in lab
    assert "layout/style issues:" in lab


def test_interactive_disconnect_pending_is_a_governed_lifecycle_state_before_revoke_wait() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "wallet.disconnect.pending",
        "mcelWalletDisconnectPending.v1",
        "interactive-wallet-disconnect",
        "permission-revoke-pending",
        "Wallet disconnect requested provider permission revoke and is waiting for the provider response.",
        "walletDisconnectPendingCount",
        "walletDisconnectPendingEvidenceCaptured",
        "walletLifecycleDisconnectPendingRequiredChecks",
        "PENDING: wallet disconnect is waiting on provider revoke; SCM containment passed",
        "wallet disconnect pending:",
    ]
    for marker in markers:
        assert marker in lab

    pending_effect = lab[lab.index('"wallet.disconnect.pending": {'):lab.index('\n          "wallet.disconnect"', lab.index('"wallet.disconnect.pending": {'))]
    assert 'triggers: ["state.walletGate"]' in pending_effect
    assert 'writes: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletDisconnectPending", "runtime.walletEvents", "runtime.walletAdapter", "runtime.externalOutcome", "runtime.evidenceStrip"]' in pending_effect
    assert 'status: "pending"' in pending_effect
    assert 'providerMutationRequested: true' in pending_effect
    assert 'runtime.txDraft.status=empty' in pending_effect

    perform_disconnect = function_body(lab, "performMcelTinyContractWalletDisconnect")
    pending_call = "commitMcelTinyContractWalletDisconnectPending(app, instance, `${reason}-disconnect-pending`)"
    revoke_call = "revokeMcelTinyContractWalletPermission()"
    assert pending_call in perform_disconnect
    assert revoke_call in perform_disconnect
    assert perform_disconnect.index(pending_call) < perform_disconnect.index(revoke_call)

    helper = function_body(lab, "commitMcelTinyContractWalletDisconnectPending")
    assert 'window.McelLabScm.runEffect(instance, "wallet.disconnect.pending"' in helper
    assert 'renderMcelTinyContractProof(target, `${reason}-disconnect-pending`)' in helper
    assert '"eth_accounts"' not in helper
    assert "wallet_switchEthereumChain" not in lab
    assert "wallet_addEthereumChain" not in lab
    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert "broadcastTransaction" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)


def test_disconnect_pending_runtime_write_is_owned_by_scm_manifest() -> None:
    lab = read_script("mcel-lab.js")

    runtime_ownership = lab[lab.index("          runtime: ["):lab.index("          ],\n          layout:", lab.index("          runtime: ["))]
    pending_effect = lab[lab.index('"wallet.disconnect.pending": {'):lab.index('\n          "wallet.disconnect"', lab.index('"wallet.disconnect.pending": {'))]
    runtime_defaults = function_body(lab, "mcelTinyContractRuntimeDefaults")

    assert '"walletDisconnectPending"' in runtime_ownership
    assert '"runtime.walletDisconnectPending"' in pending_effect
    assert "walletDisconnectPending: null" in runtime_defaults

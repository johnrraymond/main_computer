// Fixed, public-only diagnostic categories for a real Worker wallet connect.
// Never persist the exception message, MetaMask page text, wallet addresses,
// RPC payloads, signatures, URLs, or secret-bearing browser logs.
const rules = [
  [/MCEL wallet bridge refresh unavailable|legacy-marketplace-authority-not-initialized/i, 'BRIDGE_NOT_READY'],
  [/Worker session does not match the selected marketplace Hub|Wallet\/Hub session mismatch/i, 'SESSION_MISMATCH'],
  [/wallet panel not visible/i, 'WALLET_PANEL_HIDDEN'],
  [/never became handler-bound and enabled/i, 'CONNECT_CONTROL_NOT_READY'],
  [/click could not be dispatched/i, 'CONNECT_CLICK_FAILED'],
  [/click produced no connection event or busy state/i, 'CONNECT_NO_ACTIVITY'],
  [/ignored Connect Wallet because another wallet operation was active/i, 'CONNECT_BUSY'],
  [/Worker emitted connect\.failed|Worker wallet connection failed/i, 'WORKER_CONNECT_FAILED'],
  [/MetaMask approval not recognized or not enabled/i, 'METAMASK_APPROVAL_UNRECOGNIZED'],
  [/MetaMask side-panel approval is still pending/i, 'METAMASK_SIDEPANEL_UNAVAILABLE'],
  [/Worker is awaiting a MetaMask approval/i, 'METAMASK_PROMPT_MISSING'],
  [/MetaMask approval inspection stalled/i, 'METAMASK_INSPECTION_STALLED'],
  [/did not advance after connect\.wallet\.addChain\.done/i, 'NETWORK_ADD_TRANSITION_STALLED'],
  [/RPC proof did not finish/i, 'RPC_PROOF_TIMEOUT'],
  [/Worker wallet connection timed out/i, 'CONNECT_TIMEOUT'],
]
export function classifyWalletConnectFailure(message) {
  const text = String(message || '')
  for (const [pattern, category] of rules) if (pattern.test(text)) return category
  return 'CONNECT_UNCLASSIFIED'
}
export function safeWalletConnectState(state = {}) {
  const bool = value => value === true
  const choices = new Set(['none','mainnet','testnet','test','dev','slh'])
  const buttonStates = new Set(['idle','hydrating','requesting','finalizing','connected','other'])
  return {
    panelVisible:bool(state.panelVisible),
    buttonPresent:bool(state.buttonPresent),
    handlerBound:bool(state.handlerBound),
    disabled:bool(state.disabled),
    busy:bool(state.busy),
    buttonState:buttonStates.has(state.buttonState)?state.buttonState:'other',
    hubChoice:choices.has(state.hubChoice)?state.hubChoice:'unverified',
  }
}

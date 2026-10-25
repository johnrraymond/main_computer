// Test-only cached MetaMask unlock. Never print, persist, or inspect passwords.
// Refuse any page outside the verified disposable extension's home.html origin.
import {verifyWalletHomeIdentity} from './metamask_onboarding.mjs'

const EXTENSION_ID = /^[a-p]{32}$/
const EVM_ADDRESS = /^0x[0-9a-f]{40}$/i
const UNLOCK_ROUTE = /#\/(?:onboarding\/)?unlock(?:[/?]|$)/

function extensionPage(page, extensionId) {
  return !!page && typeof page.url === 'function' &&
    page.url().startsWith(`chrome-extension://${extensionId}/home.html`) &&
    !(typeof page.isClosed === 'function' && page.isClosed())
}

function unlockPage(page, extensionId) {
  return extensionPage(page, extensionId) && UNLOCK_ROUTE.test(page.url())
}

async function isShown(locator) {
  return !!locator && await locator.isVisible().catch(() => false)
}

export async function visibleUnlockForm(page, extensionId) {
  if (!unlockPage(page, extensionId)) return null
  const button = page.getByRole('button', {name: /^unlock$/i}).first()
  if (!await isShown(button)) return null
  // MetaMask v13.53 can render an ordinary password input rather than the
  // legacy unlock-password test ID. Only consider inputs on the unlock route.
  const fields = [
    page.locator('[data-testid="unlock-password"]').first(),
    page.locator('input[type="password"]').first(),
    page.getByPlaceholder(/enter your password/i).first(),
  ]
  for (const field of fields) if (await isShown(field)) return {field,button}
  return null
}

// This function is deliberately independent of the Web Worker wallet provider:
// eth_accounts/eth_blockNumber can succeed while the MetaMask UI remains locked
// and a subsequent signature is unable to proceed.
export async function ensureDisposableMetaMaskUnlocked({context, extensionId, expectedAddress, password, timeoutMs = 20_000,
  pollMs = 250, sleep = ms => new Promise(resolve => setTimeout(resolve,ms)),
  verifyHome = verifyWalletHomeIdentity, onProgress = () => {}} = {}) {
  if (!context || typeof context.pages !== 'function' || !EXTENSION_ID.test(extensionId || '') ||
      !EVM_ADDRESS.test(expectedAddress || '') || typeof password !== 'string' || !password) {
    throw new Error('Cached disposable MetaMask unlock refused: invalid test identity')
  }
  const deadline = Date.now() + timeoutMs
  let unlocked = false
  let lastState = 'no-extension-home'
  while (Date.now() < deadline) {
    const pages = context.pages().filter(page => extensionPage(page,extensionId))
    // Always unlock a visible locked tab before trusting any other cached
    // wallet-home tab. Stale tabs from the same extension may coexist.
    let pending = false
    for (const page of pages) {
      const form = await visibleUnlockForm(page, extensionId)
      if (!form) continue
      pending = true
      await form.field.fill(password)
      if (!await form.button.isEnabled().catch(() => false)) {
        throw new Error('Cached disposable MetaMask unlock refused: Unlock remains disabled')
      }
      await form.button.click({timeout: 5_000})
      unlocked = true
      lastState = 'unlock-submitted'
      onProgress('metamask.disposable-profile-unlock-submitted')
      break
    }
    // The extension can transition to a different home tab after unlocking.
    // Require that its public address matches the disposable test wallet.
    if (!pending || unlocked) {
      for (const page of pages) {
        try {
          await verifyHome(page, expectedAddress, extensionId)
          onProgress('metamask.disposable-profile-identity-verified')
          return page
        } catch { /* UI may be transitioning; no secret-bearing diagnostics. */ }
      }
    }
    if (!pending) lastState = pages.length ? 'wallet-home-not-verified' : 'no-extension-home'
    await sleep(pollMs)
  }
  throw new Error(`Cached disposable MetaMask unlock incomplete (${lastState}); no wallet profile certified`)
}

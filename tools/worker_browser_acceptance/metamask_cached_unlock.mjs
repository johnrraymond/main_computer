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
    // Once the unlock action is submitted, do not retry filling a password.
    // MetaMask may briefly retain a stale unlock route while React tears down
    // the old input. A second fill can wait 30s on an already-detached node.
    let pending = false
    if (!unlocked) {
      for (const page of pages) {
        const form = await visibleUnlockForm(page, extensionId)
        if (!form) continue
        pending = true
        try {
          // The form can disappear between isVisible() and fill(). Keep the
          // operation bounded and classify any race without propagating the
          // Playwright exception, which can contain fill(password) in plaintext.
          await form.field.fill(password, {timeout: 1_500})
        } catch {
          lastState = 'unlock-field-transitioning'
          pending = false
          break
        }
        if (!unlockPage(page, extensionId) || !await isShown(form.button)) {
          lastState = 'unlock-page-transitioning'
          pending = false
          break
        }
        if (!await form.button.isEnabled().catch(() => false)) {
          throw new Error('Cached disposable MetaMask unlock refused: Unlock remains disabled')
        }
        try {
          await form.button.click({timeout: 2_000})
        } catch (error) {
          // The synthetic fixture can report a wrong test password. Never
          // relay raw Playwright exceptions: click/fill errors may contain the
          // literal disposable password or untrusted page text.
          if (String(error?.message || '').trim() === 'incorrect test password') {
            throw new Error('Cached disposable MetaMask unlock refused: incorrect test password')
          }
          // Verify actual public wallet identity first. The button may have
          // been removed as a simultaneous unlock completed in another tab.
          lastState = 'unlock-submit-transitioning'
          pending = false
          break
        }
        unlocked = true
        lastState = 'unlock-submitted'
        onProgress('metamask.disposable-profile-unlock-submitted')
        break
      }
    }
    // The extension can switch to a new tab or route after unlocking. A home
    // is accepted ONLY when its displayed account matches the disposable
    // wallet; RPC accessibility alone never certifies MetaMask is unlocked.
    if (!pending || unlocked) {
      for (const page of context.pages().filter(page => extensionPage(page,extensionId))) {
        try {
          await verifyHome(page, expectedAddress, extensionId)
          onProgress('metamask.disposable-profile-identity-verified')
          return page
        } catch { /* UI transition or incorrect account: not a success. */ }
      }
    }
    if (!pending && !unlocked && lastState === 'no-extension-home') {
      lastState = pages.length ? 'wallet-home-not-verified' : 'no-extension-home'
    }
    if (unlocked) lastState = 'unlock-submitted-awaiting-verified-home'
    await sleep(pollMs)
  }
  throw new Error(`Cached disposable MetaMask unlock incomplete (${lastState}); no wallet profile certified`)
}

// MetaMask 13.x onboarding using the actual extension UI, not Synpress's
// legacy '#metametrics-opt-in' flow. Only for the harness's disposable wallet.
// Never record or print secret recovery phrases, passwords, input values,
// DOM snapshots, browser traces, or extension screenshots.

const NAME = {
  existing: /^(i have an existing wallet|import an existing wallet|import wallet|i already have a wallet)$/i,
  phrase: /(secret recovery phrase|recovery phrase|use.*srp)/i,
  declineMetrics: /^(no thanks|i do not agree|not now|skip)$/i,
  submitPhrase: /^(confirm secret recovery phrase|confirm|import (my )?wallet|continue)$/i,
  submitPassword: /^(import (my )?wallet|create (a )?password|continue)$/i,
  finish: /^(open (my )?wallet|go to (my )?wallet|done|got it|all done)$/i,
}
const selectors = {
  welcomeCheckbox: ['#onboarding-terms-checkbox', '[data-testid="onboarding-terms-checkbox"]'],
  termsScroll: ['[data-testid="terms-of-use-scroll-button"]', '#terms-of-use-scroll-button'],
  existing: ['#onboarding-import-wallet', '[data-testid="onboarding-import-wallet"]', '[data-testid="onboarding-existing-wallet"]'],
  phrase: ['#onboarding-import-with-srp-button', '[data-testid="onboarding-import-with-srp-button"]'],
  metrics: ['#metametrics-no-thanks', '[data-testid="metametrics-no-thanks"]'],
  phraseSubmit: ['#import-srp-confirm', '[data-testid="import-srp-confirm"]'],
  password: ['#create-password-new', '[data-testid="create-password-new"]', 'input[data-testid="create-password-new"]'],
  passwordConfirm: ['#create-password-confirm', '[data-testid="create-password-confirm"]'],
  passwordTerms: ['#create-password-terms', '[data-testid="create-password-terms"]'],
  passwordSubmit: ['#create-password-import', '#create-password-wallet', '[data-testid="create-password-import"]'],
  finish: ['#onboarding-complete-done', '[data-testid="onboarding-complete-done"]', '[data-testid="onboarding-complete-done-button"]'],
  ready: ['[data-testid="account-menu-icon"]', '[data-testid="account-list-menu"]',
          '[data-testid="network-display"]', '[data-testid="account-options-menu-button"]'],
}

function selectorLocator(page, selector) { return page.locator(selector).first() }
async function visible(locator) { return await locator.isVisible().catch(() => false) }
async function findVisible(page, candidates) {
  for (const selector of candidates) {
    const locator = selectorLocator(page, selector)
    if (await visible(locator)) return locator
  }
  return null
}
async function clickIfVisible(page, css, label) {
  const locator = await findVisible(page, css)
  if (!locator) return false
  await locator.click({timeout: 5_000})
  return label
}
async function roleClick(page, name) {
  const locator = page.getByRole('button', {name}).first()
  if (!(await visible(locator))) return false
  await locator.click({timeout: 5_000})
  return true
}
async function clickEnabledIfVisible(page, css, label) {
  const locator = await findVisible(page, css)
  if (!locator || !(await locator.isEnabled().catch(() => false))) return false
  await locator.click({timeout: 5_000})
  return label
}
async function roleClickEnabled(page, name) {
  const locator = page.getByRole('button', {name}).first()
  if (!(await visible(locator)) || !(await locator.isEnabled().catch(() => false))) return false
  await locator.click({timeout: 5_000})
  return true
}
async function checkIfVisible(page, css) {
  const locator = await findVisible(page, css)
  if (!locator) return false
  if (await locator.isChecked().catch(() => false)) return false
  await locator.check({timeout: 5_000})
  return true
}

// A fresh MetaMask install may open two home.html tabs. Matching the hash
// alone is insufficient: a stale first tab can have an onboarding route but
// no rendered controls, while a second tab displays the welcome screen.
// Select the *live UI*, not the first URL, before entering any wallet secrets.
// Prefer the furthest safely identified screen when multiple MetaMask tabs
// are live. A stale phrase tab must not outrank password/completion.
const ONBOARDING_PROGRESS = {
  welcome: 10,
  'terms-of-use-dialog': 11,
  'import-method-selection': 15,
  'analytics-consent': 18,
  'recovery-phrase-entry': 23,
  'password-creation': 30,
  'onboarding-completion': 34,
  'optional-windows-hello': 36,
  'optional-data-sharing': 37,
}
function extensionHome(extensionId) {
  if (!/^[a-p]{32}$/.test(extensionId)) throw new Error('MetaMask page selection refused: invalid extension ID')
  return `chrome-extension://${extensionId}/home.html`
}
function extensionPages(context, extensionId) {
  const home = extensionHome(extensionId)
  return context.pages().filter(page =>
    !(typeof page.isClosed === 'function' && page.isClosed()) &&
    page.url().startsWith(home))
}
async function bestLivePage(context, extensionId, {fresh} = {}) {
  const pages = extensionPages(context, extensionId)
  let best = null
  let rank = -1
  for (const page of pages) {
    if (typeof page.locator !== 'function' || typeof page.getByRole !== 'function') continue
    const label = await screenLabel(page)
    // A partially completed import outranks another tab's fresh welcome page;
    // neither an error tab nor a wallet-home tab outranks active onboarding.
    const value = fresh
      ? (ONBOARDING_PROGRESS[label] ?? -1)
      : label === 'wallet-home' ? 100 : label === 'wallet-unlock' ? 95 : (ONBOARDING_PROGRESS[label] ?? -1)
    if (value > rank) { rank = value; best = page }
  }
  return rank >= 0 ? best : null
}

// The test harness selects a responsive MetaMask tab even if the extension
// generated multiple onboarding tabs or React is still mounting. Only a
// genuinely fresh profile may be navigated to the canonical welcome route.
export async function selectMetaMaskPage(context, extensionId, {
  fresh = false, probeAttempts = 120, probeDelayMs = 250, navigateFallback = true,
} = {}) {
  const home = extensionHome(extensionId)
  let page = null
  for (let i = 0; i < probeAttempts; i++) {
    page = await bestLivePage(context, extensionId, {fresh})
    if (page) break
    // Chrome can publish the extension worker before its automatic onboarding
    // tab exists. Do not immediately create a second home.html page: that was
    // producing the competing tabs seen in the Windows screenshots. Give the
    // extension a bounded chance to open its own UI first.
    if (extensionPages(context, extensionId).length === 0 && i >= 10) break
    if (i + 1 < probeAttempts) await new Promise(resolve => setTimeout(resolve, probeDelayMs))
  }
  if (!page) {
    const candidates = extensionPages(context, extensionId)
    page = candidates.find(candidate => /#\/?onboarding\/import-with-recovery-phrase(?:[/?]|$)/.test(candidate.url())) ||
      candidates.find(candidate => /#\/?onboarding\/welcome(?:[/?]|$)/.test(candidate.url())) ||
      candidates.find(candidate => /#\/?onboarding\//.test(candidate.url())) ||
      candidates[0] || await context.newPage()
    const onWelcome = /#\/?onboarding\/welcome(?:[/?]|$)/.test(page.url())
    // Never reset an active onboarding route just because its selectors are
    // temporarily unavailable. This previously destroyed import progress.
    const onOnboarding = /#\/?onboarding\//.test(page.url())
    if (navigateFallback && ((!page.url().startsWith(home)) || (fresh && !onWelcome && !onOnboarding))) {
      await page.goto(fresh ? `${home}#/onboarding/welcome` : home, {waitUntil: 'domcontentloaded'})
    }
  }
  await page.bringToFront()
  return page
}

// Only whitelisted state, never DOM text or form data. Useful when one
// MetaMask tab is blank but another shows the welcome screen.
export async function safeExtensionTabsDiagnostics(context, extensionId, selected) {
  const pages = extensionPages(context, extensionId)
  let recognizedTabs = 0
  let welcomeTabs = 0
  const tabScreens = []
  for (const page of pages) {
    const label = typeof page.locator === 'function' && typeof page.getByRole === 'function'
      ? await screenLabel(page) : 'unrecognized-extension-screen'
    if (label !== 'unrecognized-extension-screen' && label !== 'extension-startup-error') recognizedTabs++
    if (label === 'welcome') welcomeTabs++
    tabScreens.push(label)
  }
  return {extensionTabs: pages.length, recognizedTabs, welcomeTabs, tabScreens,
    selectedTab: pages.indexOf(selected),
    selectedClosed: typeof selected?.isClosed === 'function' ? selected.isClosed() : false}
}

// The official v13.53.0 import page shown in the Windows acceptance
// screenshot uses ONE multiline editor rather than indexed SRP word fields.
// Never enter a mnemonic merely because a generic textarea exists: require
// the exact onboard-import route, its visible instruction, and the verified
// MetaMask home origin (fillPhrase separately enforces the expected ID).
function singlePhraseRoute(page) {
  return /\/home\.html#\/onboarding\/import-with-recovery-phrase(?:[/?]|$)/.test(page.url())
}
async function singlePhraseEditor(page) {
  if (!singlePhraseRoute(page) ||
      !(await fixedTextVisible(page, /enter your secret recovery phrase/i))) return null
  // Explicit v13 selectors first; the broad fallbacks are safe only under the
  // exact extension route and visible SRP instruction checked above.
  return await findVisible(page, [
    '[data-testid="srp-input-import__srp-note"]',
    'textarea[placeholder*="Add a space between each word"]',
    'textarea[aria-label*="Recovery Phrase"]',
    'textarea[aria-label*="recovery phrase"]',
    '[role="textbox"][contenteditable="true"][data-placeholder*="Add a space between each word"]',
    '[contenteditable="true"][data-placeholder*="Add a space between each word"]',
    'textarea',
    '[role="textbox"][contenteditable="true"]',
    '[contenteditable="true"]',
  ])
}

// Avoid dumping the DOM: a phrase and password can be in inputs or visible
// browser-generated attributes. Only emit fixed, non-secret screen labels.
async function fixedTextVisible(page, pattern) {
  if (!page.getByText) return false
  return await visible(page.getByText(pattern).first())
}

export async function screenLabel(page) {
  if (await fixedTextVisible(page, /MetaMask (encountered an error|had trouble starting)/i)) return 'extension-startup-error'
  // MetaMask v13.53 reuses a profile at home.html#/onboarding/unlock.
  // Treat this as a responsive cached-wallet screen, not stalled onboarding.
  if (/\/home\.html#\/(?:onboarding\/)?unlock(?:[/?]|$)/.test(page.url()) &&
      await visible(page.getByRole('button', {name: /^unlock$/i}).first()) &&
      await findVisible(page, ['[data-testid="unlock-password"]', 'input[type="password"]'])) {
    return 'wallet-unlock'
  }
  if (await isOptionalWindowsHello(page)) return 'optional-windows-hello'
  if (await isOptionalDataSharing(page)) return 'optional-data-sharing'
  if (await findVisible(page, selectors.termsScroll)) return 'terms-of-use-dialog'
  if (await findVisible(page, selectors.password)) return 'password-creation'
  if (await findVisible(page, ['input[id^="import-srp__srp-word-"]',
                                'input[data-testid^="import-srp__srp-word-"]']) ||
      await singlePhraseEditor(page)) return 'recovery-phrase-entry'
  if (singlePhraseRoute(page) &&
      await fixedTextVisible(page, /enter your secret recovery phrase/i)) {
    return 'recovery-phrase-editor-not-found'
  }
  if (await findVisible(page, selectors.welcomeCheckbox)) return 'welcome'
  if (await findVisible(page, selectors.phrase) ||
      await visible(page.getByRole('button', {name: NAME.phrase}).first())) return 'import-method-selection'
  if (await findVisible(page, selectors.existing) ||
      await visible(page.getByRole('button', {name: NAME.existing}).first())) return 'welcome'
  if (await findVisible(page, selectors.metrics)) return 'analytics-consent'
  if (await findVisible(page, selectors.finish)) return 'onboarding-completion'
  if (await findVisible(page, selectors.ready) || await hasWalletTabs(page)) return 'wallet-home' 
  return 'unrecognized-extension-screen'
}

// Diagnostics contain fixed route categories and a small whitelist of UI
// test IDs, never text content, input values, page HTML, screenshots or traces.
export async function safeScreenDiagnostics(page) {
  const url = page.url()
  const fragment = url.split('#')[1] || ''
  let route = 'other'
  if (!fragment) route = 'home-root'
  else if (/^\/?onboarding\/welcome(?:[/?]|$)/.test(fragment)) route = 'onboarding-welcome'
  else if (/^\/?onboarding\/import-with-recovery-phrase(?:[/?]|$)/.test(fragment)) route = 'onboarding-import-with-recovery-phrase'
  else if (/^\/?onboarding\//.test(fragment)) route = 'onboarding-other'
  else if (/^\/?home(?:[/?]|$)/.test(fragment)) route = 'wallet-home'
  let state = {readyState: 'unavailable', testIds: []}
  try {
    state = await page.evaluate(() => {
      const ids = Array.from(document.querySelectorAll('[data-testid]'))
        .map(element => element.getAttribute('data-testid'))
        .filter(value => typeof value === 'string' &&
          /^(?:onboarding-|terms-of-use-|metametrics-|import-srp-|create-password-|unlock-|error-)[a-z0-9_-]{0,55}$/.test(value))
      return {
        readyState: ['loading', 'interactive', 'complete'].includes(document.readyState)
          ? document.readyState : 'unknown',
        testIds: [...new Set(ids)].slice(0, 12),
      }
    })
  } catch { /* A crashed or closed tab must still yield a safe diagnosis. */ }
  const readyState = ['loading', 'interactive', 'complete'].includes(state?.readyState)
    ? state.readyState : 'unavailable'
  const testIds = Array.isArray(state?.testIds) ? state.testIds.filter(value =>
    typeof value === 'string' && /^(?:onboarding-|terms-of-use-|metametrics-|import-srp-|create-password-|unlock-|error-)[a-z0-9_-]{0,55}$/.test(value)).slice(0, 12) : []
  return {screen: await screenLabel(page), route, readyState, testIds}
}

// Optional MetaMask 13.x Windows Hello / device-passkey setup is not part
// of proving wallet import. Explicitly defer it for disposable test profiles.
// Never click an OS credential prompt or create a machine-bound passkey.
async function maybeLaterAction(page) {
  for (const role of ['link', 'button']) {
    const locator = page.getByRole(role, {name: /^maybe later$/i}).first()
    if (await visible(locator)) return locator
  }
  const text = page.getByText(/^maybe later$/i).first()
  return await visible(text) ? text : null
}
async function isOptionalWindowsHello(page) {
  if (!page.url().startsWith('chrome-extension://') ||
      !await fixedTextVisible(page, /unlock with windows hello/i)) return false
  return !!await maybeLaterAction(page)
}
async function deferOptionalWindowsHello(page, extensionId) {
  if (!page.url().startsWith(extensionId ? extensionHome(extensionId) : 'chrome-extension://') ||
      !await isOptionalWindowsHello(page)) return false
  const action = await maybeLaterAction(page)
  if (!action) throw new Error('MetaMask Windows Hello deferral refused: Maybe later unavailable')
  await action.click({timeout: 5_000})
  return true
}

// MetaMask 13.53's later "Help improve MetaMask" step is a two-choice
// consent screen, distinct from its old MetaMetrics "No thanks" dialog.
// The disposable harness opts out of BOTH sharing categories. Never guess
// whether a toggle is on; proceed only after independently verifying both.
const DATA_SHARING = [
  {name: 'Gather basic usage data', pattern: /^gather basic usage data$/i},
  {name: 'Product updates', pattern: /^product updates$/i},
]
async function isOptionalDataSharing(page) {
  return page.url().startsWith('chrome-extension://') &&
    await fixedTextVisible(page, /^help improve metamask$/i) &&
    await fixedTextVisible(page, DATA_SHARING[0].pattern) &&
    await fixedTextVisible(page, DATA_SHARING[1].pattern) &&
    await visible(page.getByRole('button', {name: /^continue$/i}).first())
}

// First use the accessible input/checkbox/switch semantics. When a styled
// card does not provide an accessible name, inspect only the specified
// checkbox's boolean state near its fixed label. No input values, text
// fields, page HTML, screenshots, or recovery phrases are read or logged.
async function visibleConsentControl(page, name) {
  for (const role of ['checkbox', 'switch']) {
    const locator = page.getByRole(role, {name: new RegExp(`^${name}$`, 'i')}).first()
    if (await visible(locator)) return locator
  }
  if (typeof page.getByLabel === 'function') {
    const locator = page.getByLabel(name, {exact: true}).first()
    if (await visible(locator)) return locator
  }
  return null
}
async function consentStateViaDom(page, label) {
  if (typeof page.evaluate !== 'function') return null
  try {
    return await page.evaluate((targetLabel) => {
      const exactText = el => el.textContent?.trim().toLowerCase() === targetLabel.toLowerCase()
      // Match the smallest element displaying the exact fixed label, not a
      // parent spanning both cards. Never return the DOM or element text.
      const labels = [...document.querySelectorAll('label,span,p,h3,h4,div')]
        .filter(exactText)
        .filter(el => ![...el.children].some(exactText))
      for (const el of labels) {
        let parent = el
        for (let depth = 0; depth < 5 && parent; depth++, parent = parent.parentElement) {
          // Reject containers also containing the other consent setting.
          const other = targetLabel === 'Gather basic usage data'
            ? 'product updates' : 'gather basic usage data'
          if (parent.textContent?.toLowerCase().includes(other)) break
          const controls = [...parent.querySelectorAll('input[type="checkbox"], [role="checkbox"], [role="switch"], [aria-checked], [aria-pressed]')]
          if (parent.matches('input[type="checkbox"], [role="checkbox"], [role="switch"], [aria-checked], [aria-pressed]')) controls.unshift(parent)
          const unique = [...new Set(controls)]
          if (unique.length !== 1) continue
          const control = unique[0]
          if (control instanceof HTMLInputElement && control.type === 'checkbox') return control.checked
          const attr = control.getAttribute('aria-checked') ?? control.getAttribute('aria-pressed') ?? control.getAttribute('data-state')
          if (attr === 'true' || attr === 'checked' || attr === 'on') return true
          if (attr === 'false' || attr === 'unchecked' || attr === 'off') return false
        }
      }
      return null
    }, label)
  } catch { return null }
}
async function consentState(page, setting) {
  const control = await visibleConsentControl(page, setting.name)
  if (control && typeof control.isChecked === 'function') {
    try {
      const checked = await control.isChecked()
      if (typeof checked === 'boolean') return {checked, control}
    } catch { /* Read-only DOM fallback below. */ }
  }
  const domState = await consentStateViaDom(page, setting.name)
  return {checked: typeof domState === 'boolean' ? domState : null, control}
}
async function declineOptionalDataSharing(page, extensionId) {
  if (!page.url().startsWith(extensionId ? extensionHome(extensionId) : 'chrome-extension://') ||
      !await isOptionalDataSharing(page)) return false
  for (const setting of DATA_SHARING) {
    const before = await consentState(page, setting)
    if (before.checked === null) {
      throw new Error(`MetaMask data-sharing opt-out refused: ${setting.name} state unavailable; no wallet profile certified`)
    }
    if (before.checked) {
      if (before.control) await before.control.click({timeout: 5_000})
      else await page.getByText(setting.pattern).first().click({timeout: 5_000})
    }
    const after = await consentState(page, setting)
    if (after.checked !== false) {
      throw new Error(`MetaMask data-sharing opt-out refused: ${setting.name} was not verified off; no wallet profile certified`)
    }
  }
  const next = page.getByRole('button', {name: /^continue$/i}).first()
  if (!await visible(next) || !await next.isEnabled().catch(() => false)) {
    throw new Error('MetaMask data-sharing opt-out refused: Continue unavailable; no wallet profile certified')
  }
  await next.click({timeout: 5_000})
  return true
}

async function hasWalletTabs(page) {
  // Tab roles discriminate the actual wallet home from onboarding pages.
  return await visible(page.getByRole('tab', {name: /^tokens$/i}).first()) &&
    await visible(page.getByRole('tab', {name: /^activity$/i}).first())
}

// Onboarding may finish in another extension tab. This is a UI identity gate,
// not the final cryptographic account proof: the Worker dapp subsequently
// verifies the complete 40-hex-character address through the real provider.
// The current MetaMask home renders an abbreviated EVM address, e.g.
// 0x0a90D...1593B. Never inspect private keys, seed inputs, or extension state.
export async function verifyWalletHomeIdentity(page, expectedAddress, extensionId) {
  if (!/^0x[0-9a-f]{40}$/i.test(expectedAddress || '')) {
    throw new Error('MetaMask wallet-home verification refused: invalid expected disposable address')
  }
  if (!page.url().startsWith(extensionHome(extensionId)) ||
      /#\/?onboarding(?:\/|$)/.test(page.url()) ||
      await screenLabel(page) !== 'wallet-home') {
    throw new Error('MetaMask wallet-home verification refused: verified extension wallet home unavailable')
  }
  // Match only the deterministic public-address abbreviation used on the
  // wallet home. Accept an ellipsis of three dots or a single ellipsis glyph.
  const address = expectedAddress.toLowerCase()
  const expectedPrefix = address.slice(0, 6)
  const expectedSuffix = address.slice(-4)
  const abbreviation = new RegExp(
    `${expectedPrefix}[0-9a-f]{0,6}\\s*(?:\\.{2,}|…|⋯)\\s*[0-9a-f]{0,6}${expectedSuffix}`, 'i')
  const exact = new RegExp(address, 'i')
  if (!await visible(page.getByText(abbreviation).first()) &&
      !await visible(page.getByText(exact).first())) {
    throw new Error('MetaMask wallet-home verification refused: displayed account does not match disposable address')
  }
  return true
}


async function acceptTerms(page) {
  if (await checkIfVisible(page, selectors.welcomeCheckbox)) return true
  const checkbox = page.getByRole('checkbox', {name: /terms (of use|and conditions)/i}).first()
  if (await visible(checkbox) && !(await checkbox.isChecked().catch(() => false))) {
    await checkbox.check({timeout: 5_000})
    return true
  }
  return false
}

async function acceptTermsDialog(page) {
  const scroll = await findVisible(page, selectors.termsScroll)
  if (!scroll) return false
  const agree = page.getByRole('button', {name: /^agree$/i}).first()
  // On recent builds Agree is disabled until the ToU scroll reaches its end.
  for (let i = 0; i < 16 && !(await agree.isEnabled().catch(() => false)); i++) {
    if (!(await visible(scroll)) || !(await scroll.isEnabled().catch(() => false))) break
    await scroll.click({timeout: 5_000})
    await page.waitForTimeout(120)
  }
  if (!(await visible(agree)) || !(await agree.isEnabled().catch(() => false))) {
    throw new Error('MetaMask terms dialog refused: Agree did not become enabled')
  }
  await agree.click({timeout: 5_000})
  return true
}

// MetaMask 13.53 converts the editor into discrete, masked word fields as
// the user types. Pressing Space after the twelfth word creates a THIRTEENTH
// empty field, which makes Continue disabled. The older MetaMask 13.17 fix
// (a final Space) therefore does not apply unconditionally to this release.
// Tab moves focus out of the editor so blur/validation can run without adding
// another recovery word. Never log phrase text or browser DOM snapshots.
async function safePhraseFieldShape(page) {
  if (typeof page.evaluate !== 'function') return ''
  try {
    const shape = await page.evaluate(() => {
      const masked = [...document.querySelectorAll('input[type="password"]')]
      return {
        fields: masked.length,
        filled: masked.filter(input => input.value.length > 0).length,
      }
    })
    if (Number.isSafeInteger(shape?.fields) && shape.fields >= 0 &&
        Number.isSafeInteger(shape?.filled) && shape.filled >= 0 &&
        shape.filled <= shape.fields) {
      return `; maskedWordFields=${shape.fields}, filledMaskedWordFields=${shape.filled}`
    }
  } catch { /* Continue to fail closed without DOM access. */ }
  return ''
}

async function submitPhrase(page) {
  let confirm = await findVisible(page, selectors.phraseSubmit)
  if (!confirm) {
    const button = page.getByRole('button', {name: NAME.submitPhrase}).first()
    if (await visible(button)) confirm = button
  }
  if (!confirm) throw new Error('MetaMask import refused: missing recovery phrase confirmation action')
  // Never click a disabled control. Allow React a short interval after blur
  // to finish validation, then return a bounded, secret-free failure.
  for (let attempt = 0; attempt < 40; attempt++) {
    if (await confirm.isEnabled().catch(() => false)) {
      await confirm.click({timeout: 5_000})
      return
    }
    await page.waitForTimeout(150)
  }
  const shape = await safePhraseFieldShape(page)
  throw new Error(`MetaMask SRP import refused: Continue remained disabled after final-word Tab/blur commit${shape}; no wallet profile certified`)
}

async function fillPhrase(page, words, extensionId) {
  // Never enter an SRP into a generic form, browser search bar, or an
  // unexpected extension origin. Both old and new field layouts are allowed.
  if (!page.url().startsWith(extensionId ? extensionHome(extensionId) : 'chrome-extension://')) {
    throw new Error('MetaMask import refused: recovery-phrase entry outside expected extension origin')
  }
  const editor = await singlePhraseEditor(page)
  if (editor) {
    // Typing the mnemonic sequentially preserves MetaMask's native input
    // handling. Do NOT append Space: with v13.53 that creates a blank 13th
    // word field. Keep focus in the verified extension's editor until typing
    // completes; then use Tab to commit/blur the final word without adding one.
    await editor.fill('')
    await editor.pressSequentially(words.join(' '), {delay: 20})
    if (!page.url().startsWith(extensionId ? extensionHome(extensionId) : 'chrome-extension://') ||
        !singlePhraseRoute(page)) {
      throw new Error('MetaMask SRP import refused: extension route changed during phrase entry; no wallet profile certified')
    }
    await page.keyboard.press('Tab')
    await submitPhrase(page)
    return
  }
  // Legacy indexed fields. Fail closed if the form is neither known layout.
  for (let i = 0; i < words.length; i++) {
    const input = await findVisible(page, [
      `input[id="import-srp__srp-word-${i}"]`,
      `input[data-testid="import-srp__srp-word-${i}"]`,
      `[data-testid="import-srp__srp-word-${i}"] input`,
      `input[aria-label="Word ${i + 1}"]`,
    ])
    if (!input) throw new Error(`MetaMask import refused: recovery word field ${i + 1} not identified`)
    await input.fill(words[i])
  }
  // A fresh Chromium profile prevents stale extra words in old 24-word forms.
  await submitPhrase(page)
}

async function fillPassword(page, password, extensionId) {
  if (!page.url().startsWith(extensionId ? extensionHome(extensionId) : 'chrome-extension://')) throw new Error('MetaMask password entry refused outside expected extension origin')
  const pass = await findVisible(page, selectors.password)
  const confirm = await findVisible(page, selectors.passwordConfirm)
  if (!pass || !confirm) throw new Error('MetaMask import refused: incomplete password form')
  await pass.fill(password)
  await confirm.fill(password)
  await checkIfVisible(page, selectors.passwordTerms)
  if (!await clickIfVisible(page, selectors.passwordSubmit, 'password-submit') &&
      !await roleClick(page, NAME.submitPassword)) {
    throw new Error('MetaMask import refused: missing password creation action')
  }
}

async function onboardingStep(page, words, password, extensionId) {
  if (await deferOptionalWindowsHello(page, extensionId)) return 'windows-hello-deferred'
  if (await declineOptionalDataSharing(page, extensionId)) return 'data-sharing-declined'
  if (await acceptTermsDialog(page)) return 'terms-dialog-accepted'
  if (await findVisible(page, selectors.ready) || await hasWalletTabs(page)) return 'ready'
  if (await findVisible(page, selectors.password)) {
    await fillPassword(page, password, extensionId)
    return 'password-submitted'
  }
  if (await singlePhraseEditor(page) ||
      await findVisible(page, ['input[id="import-srp__srp-word-0"]',
                                'input[data-testid="import-srp__srp-word-0"]',
                                'input[aria-label="Word 1"]'])) {
    await fillPhrase(page, words, extensionId)
    return 'phrase-submitted'
  }
  if (await acceptTerms(page)) return 'terms-accepted'
  if (await clickIfVisible(page, selectors.existing, 'existing-wallet') ||
      await roleClick(page, NAME.existing)) return 'existing-wallet-selected'
  if (await clickIfVisible(page, selectors.phrase, 'srp-selection') ||
      await roleClick(page, NAME.phrase)) return 'srp-selected'
  // MetaMetrics choice is optional. In newer versions it may be absent or
  // occur later in onboarding, unlike Synpress's unconditional click.
  if (await clickIfVisible(page, selectors.metrics, 'analytics-declined') ||
      await roleClick(page, NAME.declineMetrics)) return 'analytics-declined'
  if (await clickEnabledIfVisible(page, selectors.finish, 'wallet-opened') ||
      await roleClickEnabled(page, NAME.finish)) return 'completion-clicked'
  return null
}

export async function importDisposableWallet(page, mnemonic, password, {
  deadlineMs = 90_000, step = onboardingStep, context = null, extensionId = null,
} = {}) {
  if (!page.url().startsWith(extensionId ? extensionHome(extensionId) : 'chrome-extension://')) {
    throw new Error('MetaMask onboarding refused: not on an extension page')
  }
  const words = mnemonic.trim().split(/\s+/)
  if (![12, 15, 18, 21, 24].includes(words.length) ||
      !words.every(word => /^[a-z]+$/.test(word))) {
    throw new Error('MetaMask onboarding refused: invalid disposable recovery-phrase shape')
  }
  const until = Date.now() + deadlineMs
  let activePage = page
  let previous = null
  let repeats = 0
  let idle = 0
  let phraseSubmitted = false
  while (Date.now() < until) {
    // Once this run has submitted the disposable phrase, a separate live
    // wallet-home tab outranks an old completion tab, whose Done control may
    // remain disabled forever. Never do this before phrase submission.
    if (phraseSubmitted && context && extensionId) {
      const candidate = await bestLivePage(context, extensionId, {fresh: false})
      if (candidate && candidate !== activePage && await screenLabel(candidate) === 'wallet-home') {
        activePage = candidate
        idle = 0
        previous = null
      }
    }
    // If React navigated or a duplicate onboarding tab replaced this one,
    // recover the live page without restarting onboarding or leaking secrets.
    if (context && extensionId && (typeof activePage.isClosed === 'function' && activePage.isClosed())) {
      const next = await bestLivePage(context, extensionId, {fresh: true})
      if (next) { activePage = next; idle = 0; previous = null }
    }
    const action = await step(activePage, words, password, extensionId)
    if (action === 'ready') {
      if (!phraseSubmitted) throw new Error('MetaMask onboarding refused: wallet home appeared before disposable recovery phrase was imported; no wallet profile certified')
      return activePage
    }
    if (action === 'phrase-submitted') phraseSubmitted = true
    idle = action ? 0 : idle + 1
    if (!action && context && extensionId && idle >= 2) {
      const next = await bestLivePage(context, extensionId, {fresh: true})
      if (next && next !== activePage) {
        activePage = next
        idle = 0
        previous = null
        continue
      }
    }
    // Chromium may take longer than the first tab-probe window to mount the
    // extension after starting two onboarding tabs. Continue probing within
    // a finite bound; never assume a blank tab implies a completed wallet.
    if (idle > 110) break
    if (action === previous && action !== null) repeats++
    else repeats = 0
    if (repeats > 30) break
    previous = action
    await activePage.waitForTimeout(450)
  }
  const detail = await safeScreenDiagnostics(activePage)
  const tabs = context && extensionId
    ? await safeExtensionTabsDiagnostics(context, extensionId, activePage) : null
  const tabSummary = tabs ? `, extensionTabs=${tabs.extensionTabs}, recognizedTabs=${tabs.recognizedTabs}, welcomeTabs=${tabs.welcomeTabs}, selectedTab=${tabs.selectedTab}, selectedClosed=${tabs.selectedClosed}` : ''
  throw new Error(`MetaMask onboarding incomplete (screen=${detail.screen}, route=${detail.route}, readyState=${detail.readyState}, testIds=${detail.testIds.join('|') || 'none'}, lastAction=${previous || 'none'}${tabSummary}); no wallet profile certified`)
}

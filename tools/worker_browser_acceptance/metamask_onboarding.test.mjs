import test from 'node:test'
import assert from 'node:assert/strict'
import {importDisposableWallet, screenLabel} from './metamask_onboarding.mjs'

const mnemonic = Array(12).fill('sample').join(' ')
const password = 'Disposable@12345'

function fakePage({withMetrics = false, withWindowsHello = false, withDataSharing = false, usageChecked = true, productChecked = false, unknownConsent = false, startAt = null, stuck = false, url = 'chrome-extension://test/home.html'} = {}) {
  let state = startAt || (stuck ? 'unrecognized' : 'welcome')
  let termsAccepted = false
  const consent = {usage: usageChecked, product: productChecked}
  const inputs = {}
  const actions = []
  const detect = (selector) => {
    if (state === 'welcome') return selector.includes('onboarding-terms-checkbox') || selector.includes('onboarding-import-wallet')
    if (state === 'method') return selector.includes('onboarding-import-with-srp-button')
    if (state === 'metrics') return selector.includes('metametrics-no-thanks')
    if (state === 'phrase') return /^input.*import-srp__srp-word-/.test(selector) || selector.includes('import-srp-confirm')
    if (state === 'password') return selector.includes('create-password-new') || selector.includes('create-password-confirm') ||
      selector.includes('create-password-terms') || selector.includes('create-password-import')
    if (state === 'complete') return selector.includes('onboarding-complete-done')
    if (state === 'ready') return selector.includes('account-menu-icon')
    return false
  }
  const node = (selector) => ({
    first() {return this},
    async isVisible() {return detect(selector)},
    async isChecked() {return termsAccepted},
    async isEnabled() {return true},
    async check() {actions.push('check:' + selector);termsAccepted = true},
    async click() {
      actions.push('click:' + selector)
      if (state === 'welcome') {assert.ok(termsAccepted);state = 'method'}
      else if (state === 'method') state = withMetrics ? 'metrics' : 'phrase'
      else if (state === 'metrics') state = 'phrase'
      else if (state === 'phrase') {assert.equal(Object.keys(inputs).length, 12);state = 'password'}
      else if (state === 'password') {assert.equal(inputs.password, password);state = withWindowsHello ? 'hello' : (withDataSharing ? 'data' : 'complete')}
      else if (state === 'complete') state = 'ready'
    },
    async fill(value) {
      actions.push('fill:' + selector.replace(/.*(import-srp__srp-word-|create-password-).*/, '$1'))
      if (state === 'phrase') inputs[selector.match(/word-(\d+)/)[1]] = value
      else if (state === 'password') inputs.password = value
    },
  })
  return {
    url() {return url},
    locator: node,
    getByRole: (role, {name} = {}) => {
      const consentKind = name?.test?.('Gather basic usage data') ? 'usage' :
        name?.test?.('Product updates') ? 'product' : null
      return {
        first() {return this},
        isVisible: async () => (state === 'hello' && role === 'link' && name?.test('Maybe later')) ||
          (state === 'data' && role === 'button' && name?.test('Continue')) ||
          (state === 'data' && !unknownConsent && role === 'checkbox' && consentKind !== null),
        async isChecked() {assert.equal(state, 'data');return consent[consentKind]},
        async isEnabled() {return true},
        async click() {
          if (state === 'hello') {
            actions.push('windows-hello-maybe-later')
            state = withDataSharing ? 'data' : 'ready'
          } else if (state === 'data' && role === 'checkbox') {
            actions.push('consent-toggle-' + consentKind)
            consent[consentKind] = !consent[consentKind]
          } else if (state === 'data' && role === 'button') {
            assert.equal(consent.usage, false)
            assert.equal(consent.product, false)
            actions.push('consent-continue')
            state = 'complete'
          } else throw new Error('Unexpected role click')
        },
      }
    },
    getByText: pattern => ({
      first() {return this},
      isVisible: async () => (state === 'hello' && pattern?.test('Unlock with Windows Hello')) ||
        (state === 'data' && ['Help improve MetaMask', 'Gather basic usage data', 'Product updates']
          .some(label => pattern?.test(label))),
    }),
    waitForTimeout: async () => {},
    actions,
    getState: () => state,
    getConsent: () => ({...consent}),
    setConsent: (kind, value) => {consent[kind] = value},
  }
}

test('MetaMask v13 import without old mandatory MetaMetrics checkbox', async () => {
  const page = fakePage()
  await importDisposableWallet(page, mnemonic, password)
  assert.equal(page.getState(), 'ready')
  assert.ok(!page.actions.some(value => value.includes('metametrics-opt-in')))
  assert.ok(page.actions.some(value => value.includes('create-password-import')))
})

test('optional MetaMetrics screen is explicitly declined', async () => {
  const page = fakePage({withMetrics: true})
  await importDisposableWallet(page, mnemonic, password)
  assert.ok(page.actions.some(value => value.includes('metametrics-no-thanks')))
})

test('unrecognized screen fails closed with label, not secrets', async () => {
  const page = fakePage({stuck: true})
  assert.equal(await screenLabel(page), 'unrecognized-extension-screen')
  await assert.rejects(importDisposableWallet(page, mnemonic, password), error =>
    error.message.includes('unrecognized-extension-screen') && !error.message.includes(mnemonic))
})

test('wallet import refuses a non-extension origin', async () => {
  const page = fakePage({url: 'http://127.0.0.1:8765/applications/worker'})
  await assert.rejects(importDisposableWallet(page, mnemonic, password), /not on an extension page/)
})

test('invalid recovery phrases refused without passing to browser', async () => {
  const page = fakePage()
  await assert.rejects(importDisposableWallet(page, 'invalid phrase', password), /invalid disposable recovery-phrase shape/)
  assert.deepEqual(page.actions, [])
})

// Browser startup fixtures specifically guard the original regression:
// opening home.html with no onboarding hash, despite MetaMask opening its own
// onboarding page when an extension is installed into a fresh profile.
test('fresh install prefers MetaMask-created onboarding tab without overwriting it', async () => {
  const id = 'a'.repeat(32)
  const page = {
    url: () => `chrome-extension://${id}/home.html#/onboarding/welcome`,
    async goto() {throw new Error('Existing onboarding page should not be reset')},
    async bringToFront() {this.focused = true},
  }
  const context = {pages: () => [page], async newPage() {throw new Error('Unexpected new page')}}
  const {selectMetaMaskPage} = await import('./metamask_onboarding.mjs')
  assert.equal(await selectMetaMaskPage(context, id, {fresh: true, probeAttempts: 1}), page)
  assert.equal(page.focused, true)
})

test('fresh install without a known onboarding tab opens canonical welcome route', async () => {
  const id = 'b'.repeat(32)
  const navigations = []
  const page = {
    url: () => 'about:blank',
    async goto(url) {navigations.push(url)},
    async bringToFront() {},
  }
  const context = {pages: () => [], async newPage() {return page}}
  const {selectMetaMaskPage} = await import('./metamask_onboarding.mjs')
  assert.equal(await selectMetaMaskPage(context, id, {fresh: true, probeAttempts: 1}), page)
  assert.deepEqual(navigations, [`chrome-extension://${id}/home.html#/onboarding/welcome`])
})

test('bare home.html on fresh profile is redirected to onboarding, not assumed ready', async () => {
  const id = 'c'.repeat(32)
  const navigations = []
  const page = {
    url: () => `chrome-extension://${id}/home.html`,
    async goto(url) {navigations.push(url)},
    async bringToFront() {},
  }
  const {selectMetaMaskPage} = await import('./metamask_onboarding.mjs')
  await selectMetaMaskPage({pages: () => [page]}, id, {fresh: true, probeAttempts: 1})
  assert.deepEqual(navigations, [`chrome-extension://${id}/home.html#/onboarding/welcome`])
})

test('non-fresh profile visits the wallet root and refuses invalid extension IDs', async () => {
  const id = 'd'.repeat(32)
  const urls = []
  const page = {url: () => 'about:blank', async goto(url) {urls.push(url)}, async bringToFront() {}}
  const context = {pages: () => [], async newPage() {return page}}
  const {selectMetaMaskPage} = await import('./metamask_onboarding.mjs')
  await selectMetaMaskPage(context, id, {fresh: false, probeAttempts: 1})
  assert.deepEqual(urls, [`chrome-extension://${id}/home.html`])
  await assert.rejects(selectMetaMaskPage(context, 'wrong', {fresh: true}), /invalid extension ID/)
})

test('terms dialog scrolls to enable Agree before touching import controls', async () => {
  const page = fakePage()
  const previousLocator = page.locator
  const previousGetByRole = page.getByRole
  const actions = page.actions
  let scrolled = 0
  let agreed = false
  page.locator = selector => {
    if (selector.includes('terms-of-use-scroll-button')) return {
      first() {return this},
      isVisible: async () => !agreed,
      isEnabled: async () => scrolled < 2,
      async click() {actions.push('terms-scroll'); scrolled++},
    }
    return previousLocator(selector)
  }
  page.getByRole = (role, {name} = {}) => {
    if (role === 'button' && name?.test('Agree')) return {
      first() {return this},
      isVisible: async () => !agreed,
      isEnabled: async () => scrolled >= 2,
      async click() {actions.push('terms-agree'); agreed = true},
    }
    return previousGetByRole(role, {name})
  }
  await importDisposableWallet(page, mnemonic, password)
  assert.deepEqual(actions.slice(0, 3), ['terms-scroll', 'terms-scroll', 'terms-agree'])
  assert.equal(page.getState(), 'ready')
})

test('diagnostics only return allowlisted screen categories and test IDs', async () => {
  const {safeScreenDiagnostics} = await import('./metamask_onboarding.mjs')
  const page = fakePage({stuck: true, url: 'chrome-extension://test/home.html#/onboarding/welcome'})
  page.evaluate = async () => ({
    readyState: 'complete',
    testIds: ['onboarding-import-wallet', 'secret phrase should never leak', 'create-password-new', 'error-x'],
    password: 'DO_NOT_LOG_PASSWORD',
  })
  const result = await safeScreenDiagnostics(page)
  assert.deepEqual(result, {
    screen: 'unrecognized-extension-screen', route: 'onboarding-welcome', readyState: 'complete',
    testIds: ['onboarding-import-wallet', 'create-password-new', 'error-x'],
  })
  assert.ok(!JSON.stringify(result).includes('DO_NOT_LOG_PASSWORD'))
})

test('startup error is classified without printing extension DOM or wallet secrets', async () => {
  const {safeScreenDiagnostics} = await import('./metamask_onboarding.mjs')
  const page = fakePage({stuck: true, url: 'chrome-extension://test/home.html#/onboarding/welcome'})
  page.getByText = () => ({first: () => ({isVisible: async () => true})})
  page.evaluate = async () => ({readyState: 'interactive', testIds: []})
  const result = await safeScreenDiagnostics(page)
  assert.equal(result.screen, 'extension-startup-error')
  assert.equal(result.route, 'onboarding-welcome')
})

// Regression for the real Windows report: two MetaMask tabs were visible,
// and the harness described an onboarding-other/unrecognized page despite
// a second tab rendering "I have an existing wallet" on the welcome route.
test('selects rendered existing-wallet welcome over earlier stale MetaMask onboarding tab', async () => {
  const {selectMetaMaskPage, safeExtensionTabsDiagnostics} = await import('./metamask_onboarding.mjs')
  const id = 'd'.repeat(32)
  function tab(route, hasExistingButton) {
    const url = `chrome-extension://${id}/home.html#${route}`
    return {
      url: () => url,
      isClosed: () => false,
      locator: () => ({first() {return this}, isVisible: async () => false}),
      getByRole: (role, {name} = {}) => ({
        first() {return this},
        isVisible: async () => role === 'button' && hasExistingButton && name?.test('I have an existing wallet'),
      }),
      async goto() {throw new Error('A rendered onboarding page must not be reset')},
      async bringToFront() {this.focused = true},
    }
  }
  const stale = tab('/onboarding/some-other-route', false)
  const welcome = tab('/onboarding/welcome', true)
  const context = {pages: () => [stale, welcome], async newPage() {throw new Error('Do not open duplicate tab')}}
  const selected = await selectMetaMaskPage(context, id, {fresh: true, probeAttempts: 2, probeDelayMs: 0})
  assert.equal(selected, welcome)
  assert.equal(welcome.focused, true)
  assert.equal(stale.focused, undefined)
  const diagnostics = await safeExtensionTabsDiagnostics(context, id, selected)
  assert.deepEqual(diagnostics, {
    extensionTabs: 2, recognizedTabs: 1, welcomeTabs: 1, tabScreens: ['unrecognized-extension-screen', 'welcome'], selectedTab: 1, selectedClosed: false,
  })
  assert.ok(!JSON.stringify(diagnostics).includes('chrome-extension://'))
})

test('waits for welcome button to render rather than immediately selecting stale tab', async () => {
  const {selectMetaMaskPage} = await import('./metamask_onboarding.mjs')
  const id = 'e'.repeat(32)
  let probes = 0
  const page = {
    url: () => `chrome-extension://${id}/home.html#/onboarding/welcome`,
    isClosed: () => false,
    locator: () => ({first() {return this}, isVisible: async () => false}),
    getByRole: (role, {name} = {}) => ({
      first() {return this},
      isVisible: async () => role === 'button' && name?.test('I have an existing wallet') && ++probes >= 3,
    }),
    async goto() {throw new Error('Do not reload delayed React render')},
    async bringToFront() {},
  }
  const context = {pages: () => [page]}
  assert.equal(await selectMetaMaskPage(context, id, {
    fresh: true, probeAttempts: 5, probeDelayMs: 0,
  }), page)
  assert.ok(probes >= 3)
})

test('refuses wallet secret entry under an extension ID different from the verified installation', async () => {
  const page = fakePage({url: `chrome-extension://${'a'.repeat(32)}/home.html#/onboarding/welcome`})
  await assert.rejects(importDisposableWallet(page, mnemonic, password, {extensionId: 'b'.repeat(32)}),
    /not on an extension page/)
  assert.deepEqual(page.actions, [])
})

// Regression for the v13.53.0 Windows screenshot: the new import route has a
// single large editor, not 12 indexed inputs. A stale tab may be listed first.
function v13SinglePhrasePage(id, {editorKind = 'textarea', phrasePrompt = true,
  route = '/onboarding/import-with-recovery-phrase', commitEnables = true, editorDetachesOnType = false} = {}) {
  let stage = 'phrase'
  let typed = false
  let committed = false
  const actions = []
  const address = `chrome-extension://${id}/home.html#${route}`
  const field = (selector) => ({
    first() {return this},
    async isVisible() {
      if (stage === 'phrase') {
        if (editorDetachesOnType && typed) return false
        if (editorKind === 'textarea') return selector === 'textarea[placeholder*="Add a space between each word"]'
        if (editorKind === 'testid') return selector === '[data-testid="srp-input-import__srp-note"]'
        return selector === '[role="textbox"][contenteditable="true"][data-placeholder*="Add a space between each word"]'
      }
      if (stage === 'password') return ['#create-password-new', '#create-password-confirm', '#create-password-import'].includes(selector)
      return stage === 'ready' && selector === '[data-testid="account-menu-icon"]'
    },
    async fill(value) {
      if (stage === 'phrase') {assert.equal(value, '');actions.push('phrase-cleared')}
      else if (stage === 'password') {assert.equal(value, password);actions.push('password-filled')}
    },
    async pressSequentially(value, options) {
      assert.equal(stage, 'phrase')
      assert.equal(value, mnemonic)
      assert.ok(options.delay >= 1)
      typed = true
      actions.push('phrase-typed')
    },
    async press() {
      throw new Error('Obsolete editor locator was re-resolved after typing')
    },
    async click() {
      if (stage === 'password') {stage = 'ready'; actions.push('password-submitted')}
    },
    async isChecked() {return true},
    async isEnabled() {return true},
  })
  return {
    url: () => address,
    isClosed: () => false,
    locator: field,
    keyboard: {async press(key) {
      assert.equal(key, 'Tab', 'v13.53 must never append a thirteenth blank word')
      assert.ok(typed)
      assert.equal(stage, 'phrase')
      committed = commitEnables
      actions.push('final-word-blurred')
    }},
    getByText: pattern => ({first() {return this}, isVisible: async () =>
      stage === 'phrase' && phrasePrompt && pattern.test('Enter your Secret Recovery Phrase')}),
    getByRole: (role, {name} = {}) => ({
      first() {return this},
      isVisible: async () => role === 'button' && stage === 'phrase' && name?.test('Continue'),
      isEnabled: async () => committed,
      async click() {
        assert.ok(committed, 'Continue must never be clicked while disabled')
        stage = 'password';actions.push('phrase-continued')
      },
    }),
    async goto() {throw new Error('Never navigate away from a running import')},
    async bringToFront() {actions.push('focused')},
    async waitForTimeout() {},
    getState: () => stage,
    actions,
  }
}

test('v13.53 single textarea recovery form is recognized and imported without exposing secrets', async () => {
  const id = 'f'.repeat(32)
  const page = v13SinglePhrasePage(id)
  assert.equal(await screenLabel(page), 'recovery-phrase-entry')
  await importDisposableWallet(page, mnemonic, password, {extensionId: id})
  assert.equal(page.getState(), 'ready')
  assert.deepEqual(page.actions, ['phrase-cleared', 'phrase-typed', 'final-word-blurred', 'phrase-continued', 'password-filled', 'password-filled', 'password-submitted'])
  assert.ok(!page.actions.join(' ').includes(mnemonic))
})

test('v13.53 official SRP test ID uses keyboard input without introducing a 13th word', async () => {
  const id = 'o'.repeat(32)
  const page = v13SinglePhrasePage(id, {editorKind: 'testid'})
  await importDisposableWallet(page, mnemonic, password, {extensionId: id})
  assert.equal(page.getState(), 'ready')
  assert.ok(page.actions.indexOf('final-word-blurred') < page.actions.indexOf('phrase-continued'))
})

test('v13 SRP editor may detach after typing; page keyboard can blur without re-resolving editor', async () => {
  const id = 'b'.repeat(32)
  const page = v13SinglePhrasePage(id, {editorKind: 'testid', editorDetachesOnType: true})
  await importDisposableWallet(page, mnemonic, password, {extensionId: id})
  assert.equal(page.getState(), 'ready')
  assert.ok(page.actions.indexOf('final-word-blurred') < page.actions.indexOf('phrase-continued'))
  assert.ok(!page.actions.join(' ').includes(mnemonic))
})

test('SRP keyboard blur refuses navigation away from the verified form', async () => {
  const id = 'c'.repeat(32)
  const page = v13SinglePhrasePage(id)
  const initialLocator = page.locator
  const initialUrl = page.url
  let navigated = false
  page.url = () => navigated
    ? `chrome-extension://${id}/home.html#/onboarding/welcome`
    : initialUrl()
  page.locator = selector => {
    const result = initialLocator(selector)
    if (selector === '[data-testid="srp-input-import__srp-note"]' || selector === 'textarea[placeholder*="Add a space between each word"]') {
      const initialType = result.pressSequentially
      result.pressSequentially = async (...args) => {await initialType(...args);navigated = true}
    }
    return result
  }
  await assert.rejects(importDisposableWallet(page, mnemonic, password, {extensionId: id}),
    /extension route changed during phrase entry; no wallet profile certified/)
  assert.ok(!page.actions.includes('final-word-blurred'))
  assert.ok(!page.actions.join(' ').includes(mnemonic))
})

test('disabled SRP Continue after blur refuses without clicking or exposing recovery phrase', async () => {
  const id = 'p'.repeat(32)
  const page = v13SinglePhrasePage(id, {commitEnables: false})
  await assert.rejects(importDisposableWallet(page, mnemonic, password, {extensionId: id}),
    error => error.message.includes('Continue remained disabled after final-word Tab/blur commit') &&
      error.message.includes('no wallet profile certified') &&
      !error.message.includes(mnemonic))
  assert.ok(!page.actions.includes('phrase-continued'))
})

test('v13 single contenteditable recovery form accepts the same verified route', async () => {
  const id = 'g'.repeat(32)
  const page = v13SinglePhrasePage(id, {editorKind: 'contenteditable'})
  assert.equal(await screenLabel(page), 'recovery-phrase-entry')
  await importDisposableWallet(page, mnemonic, password, {extensionId: id})
  assert.equal(page.getState(), 'ready')
})

test('selects the v13 import tab over an earlier unresponsive extension tab', async () => {
  const {selectMetaMaskPage, safeExtensionTabsDiagnostics} = await import('./metamask_onboarding.mjs')
  const id = 'h'.repeat(32)
  const stale = {
    url: () => `chrome-extension://${id}/home.html#/onboarding/other`,
    isClosed: () => false,
    locator: () => ({first() {return this},isVisible: async () => false}),
    getByRole: () => ({first() {return this},isVisible: async () => false}),
    async goto() {throw new Error('Unexpected navigation')},
  }
  const live = v13SinglePhrasePage(id)
  const context = {pages: () => [stale,live]}
  const selected = await selectMetaMaskPage(context, id, {fresh: true, probeAttempts: 1})
  assert.equal(selected, live)
  assert.deepEqual((await safeExtensionTabsDiagnostics(context, id, selected)).tabScreens,
    ['unrecognized-extension-screen', 'recovery-phrase-entry'])
  await importDisposableWallet(selected, mnemonic, password, {extensionId: id, context})
  assert.equal(live.getState(), 'ready')
})

test('v13 secret entry refuses unverified extension origin even with a valid form', async () => {
  const page = v13SinglePhrasePage('i'.repeat(32))
  await assert.rejects(importDisposableWallet(page, mnemonic, password,
    {extensionId: 'j'.repeat(32)}), /not on an extension page/)
  assert.ok(!page.actions.includes('phrase-typed'))
})

test('v13 secret entry requires visible recovery phrase instruction, not only a textbox', async () => {
  const id = 'k'.repeat(32)
  const page = v13SinglePhrasePage(id, {phrasePrompt: false})
  assert.equal(await screenLabel(page), 'unrecognized-extension-screen')
  await assert.rejects(importDisposableWallet(page, mnemonic, password,
    {extensionId: id, deadlineMs: 1000}), /no wallet profile certified/)
  assert.ok(!page.actions.includes('phrase-typed'))
})

test('v13 secret entry refuses generic textarea outside known import route', async () => {
  const id = 'l'.repeat(32)
  const page = v13SinglePhrasePage(id, {route: '/onboarding/welcome'})
  assert.equal(await screenLabel(page), 'unrecognized-extension-screen')
  await assert.rejects(importDisposableWallet(page, mnemonic, password,
    {extensionId: id, deadlineMs: 1000}), /no wallet profile certified/)
  assert.ok(!page.actions.includes('phrase-typed'))
})

test('a stale import tab cannot displace a later password-creation tab', async () => {
  const {selectMetaMaskPage} = await import('./metamask_onboarding.mjs')
  const id = 'm'.repeat(32)
  const phrase = v13SinglePhrasePage(id)
  const passwordPage = {
    url: () => `chrome-extension://${id}/home.html#/onboarding/create-password`,
    isClosed: () => false,
    locator: selector => ({first() {return this}, isVisible: async () =>
      selector === '#create-password-new'}),
    getByRole: () => ({first() {return this}, isVisible: async () => false}),
    async bringToFront() {this.focused = true},
  }
  const selected = await selectMetaMaskPage({pages: () => [phrase, passwordPage]}, id,
    {fresh: true, probeAttempts: 1})
  assert.equal(selected, passwordPage)
  assert.equal(passwordPage.focused, true)
})

test('a pre-existing wallet home never certifies an unimported disposable phrase', async () => {
  const page = fakePage({stuck: true})
  await assert.rejects(importDisposableWallet(page, mnemonic, password,
    {step: async () => 'ready'}), /wallet home appeared before disposable recovery phrase was imported/)
  assert.deepEqual(page.actions, [])
})

test('diagnostics identify the current v13 import route without reading the phrase', async () => {
  const {safeScreenDiagnostics} = await import('./metamask_onboarding.mjs')
  const id = 'n'.repeat(32)
  const page = v13SinglePhrasePage(id)
  page.evaluate = async () => ({readyState: 'complete', testIds: ['secret-phrase-very-private']})
  const diagnostics = await safeScreenDiagnostics(page)
  assert.equal(diagnostics.screen, 'recovery-phrase-entry')
  assert.equal(diagnostics.route, 'onboarding-import-with-recovery-phrase')
  assert.deepEqual(diagnostics.testIds, [])
  assert.ok(!JSON.stringify(diagnostics).includes('secret-phrase'))
})

// Reproduces the actual Windows screenshot: Space after 12 correctly entered
// words creates a blank 13th field. Tab must not create an additional field.
test('v13.53 twelve-word import never creates the empty thirteenth field', async () => {
  const id = 'p'.repeat(32)
  const page = v13SinglePhrasePage(id, {editorKind: 'testid'})
  const keyboard = page.keyboard
  let blankThirteenthField = false
  page.keyboard = {async press(key) {
    if (key === 'Space') blankThirteenthField = true
    await keyboard.press(key)
  }}
  await importDisposableWallet(page, mnemonic, password, {extensionId: id})
  assert.equal(page.getState(), 'ready')
  assert.equal(blankThirteenthField, false)
  assert.ok(!page.actions.some(action => action.includes('Space')))
})

test('diagnostic reports word field counts only, never recovery words', async () => {
  const id = 'o'.repeat(32)
  const page = v13SinglePhrasePage(id, {commitEnables: false})
  page.evaluate = async () => ({fields: 13, filled: 12})
  await assert.rejects(importDisposableWallet(page, mnemonic, password, {extensionId: id}), error =>
    error.message.includes('maskedWordFields=13, filledMaskedWordFields=12') &&
    !error.message.includes(mnemonic) &&
    !error.message.includes(password))
  assert.ok(!page.actions.includes('phrase-continued'))
})

test('recovery validation does not leak malformed DOM shape values', async () => {
  const id = 'n'.repeat(32)
  const page = v13SinglePhrasePage(id, {commitEnables: false})
  page.evaluate = async () => ({fields: mnemonic, filled: password})
  await assert.rejects(importDisposableWallet(page, mnemonic, password, {extensionId: id}), error =>
    !error.message.includes(mnemonic) && !error.message.includes(password) &&
    !error.message.includes('maskedWordFields='))
})

// Actual October 23 Windows screenshot: valid phrase import and password
// completion are followed by an optional Windows Hello/passkey upsell.
test('skips optional Windows Hello with Maybe later, without invoking OS passkeys', async () => {
  const page = fakePage({withWindowsHello: true})
  const result = await importDisposableWallet(page, mnemonic, password)
  assert.equal(result, page)
  assert.equal(page.getState(), 'ready')
  assert.ok(page.actions.includes('windows-hello-maybe-later'))
  assert.ok(!page.actions.some(action => /set up windows hello/i.test(action)))
})

test('recognizes Windows Hello as an optional onboarding screen, not a completed wallet', async () => {
  const page = fakePage({withWindowsHello: true})
  let wasHello = false
  const oldGetByText = page.getByText
  page.getByText = pattern => {
    const locator = oldGetByText(pattern)
    const oldVisible = locator.isVisible
    locator.isVisible = async () => {
      const answer = await oldVisible()
      if (answer) wasHello = true
      return answer
    }
    return locator
  }
  await importDisposableWallet(page, mnemonic, password)
  assert.equal(wasHello, true)
})

test('waits beyond initial 20 tab probes for a delayed extension renderer', async () => {
  const {selectMetaMaskPage} = await import('./metamask_onboarding.mjs')
  const id = 'a'.repeat(32)
  let probes = 0
  const page = {
    url: () => `chrome-extension://${id}/home.html#/onboarding/welcome`,
    isClosed: () => false,
    locator: () => ({first() {return this}, isVisible: async () => false}),
    getByRole: (role, {name} = {}) => ({
      first() {return this},
      isVisible: async () => role === 'button' && name?.test('I have an existing wallet') && ++probes >= 36,
    }),
    async goto() {throw new Error('Do not reset a temporarily unreadable onboarding route')},
    async bringToFront() {this.focused = true},
  }
  const selected = await selectMetaMaskPage({pages: () => [page]}, id, {
    fresh: true, probeAttempts: 45, probeDelayMs: 0,
  })
  assert.equal(selected, page)
  assert.equal(page.focused, true)
  assert.ok(probes >= 36)
})

test('an unrecognized startup page may recover after 40 idle states without premature refusal', async () => {
  const page = fakePage({stuck: true})
  let calls = 0
  const result = await importDisposableWallet(page, mnemonic, password, {
    step: async () => {
      ++calls
      if (calls <= 52) return null
      if (calls === 53) return 'phrase-submitted'
      return 'ready'
    },
    deadlineMs: 1000,
  })
  assert.equal(result, page)
  assert.equal(calls, 54)
})

test('waits for the extension to open its own tab instead of creating a duplicate', async () => {
  const {selectMetaMaskPage} = await import('./metamask_onboarding.mjs')
  const id = 'p'.repeat(32)
  let checks = 0
  const page = {
    url: () => `chrome-extension://${id}/home.html#/onboarding/welcome`,
    isClosed: () => false,
    locator: () => ({first() {return this}, isVisible: async () => false}),
    getByRole: (role, {name} = {}) => ({
      first() {return this},
      isVisible: async () => role === 'button' && name?.test('I have an existing wallet'),
    }),
    async bringToFront() {this.focused = true},
  }
  const context = {
    pages() {++checks;return checks >= 5 ? [page] : []},
    async newPage() {throw new Error('Do not open a competing tab')},
  }
  assert.equal(await selectMetaMaskPage(context, id,
    {fresh: true, probeAttempts: 20, probeDelayMs: 0}), page)
  assert.equal(page.focused, true)
})

// October 23 screenshot: newer MetaMask builds display a second optional
// telemetry screen *after* wallet import. Basic usage is checked by default,
// and product updates may be independently checked or unchecked.
test('new MetaMask Help improve screen opts out of usage data and product updates', async () => {
  const page = fakePage({withWindowsHello: true, withDataSharing: true, usageChecked: true, productChecked: true})
  const result = await importDisposableWallet(page, mnemonic, password)
  assert.equal(result, page)
  assert.equal(page.getState(), 'ready')
  assert.deepEqual(page.getConsent(), {usage: false, product: false})
  assert.ok(page.actions.includes('windows-hello-maybe-later'))
  assert.ok(page.actions.includes('consent-toggle-usage'))
  assert.ok(page.actions.includes('consent-toggle-product'))
  assert.ok(page.actions.includes('consent-continue'))
  assert.ok(page.actions.indexOf('consent-toggle-product') < page.actions.indexOf('consent-continue'))
})

test('new consent screen leaves already-disabled permissions off', async () => {
  const page = fakePage({withDataSharing: true, usageChecked: false, productChecked: false})
  await importDisposableWallet(page, mnemonic, password)
  assert.deepEqual(page.getConsent(), {usage: false, product: false})
  assert.ok(!page.actions.some(x => x.startsWith('consent-toggle')))
  assert.ok(page.actions.includes('consent-continue'))
})

test('new consent screen refuses unknown toggle state instead of guessing', async () => {
  const page = fakePage({withDataSharing: true, unknownConsent: true})
  await assert.rejects(importDisposableWallet(page, mnemonic, password), error =>
    error.message.includes('Gather basic usage data state unavailable') &&
    error.message.includes('no wallet profile certified') &&
    !error.message.includes(mnemonic) && !error.message.includes(password))
  assert.ok(!page.actions.includes('consent-continue'))
})

test('new consent screen has a distinct recognized screen label', async () => {
  const page = fakePage({startAt: 'data', withDataSharing: true})
  assert.equal(await screenLabel(page), 'optional-data-sharing')
})

test('data-sharing consent takes precedence over a stale recovery phrase tab', async () => {
  const {selectMetaMaskPage} = await import('./metamask_onboarding.mjs')
  const id = 'p'.repeat(32)
  const oldPhrase = v13SinglePhrasePage(id)
  const data = fakePage({startAt: 'data', withDataSharing: true,
    url: `chrome-extension://${id}/home.html#/onboarding/metrics`})
  data.isClosed = () => false
  data.bringToFront = async () => {}
  const chosen = await selectMetaMaskPage({pages: () => [oldPhrase, data]}, id,
    {fresh: true, probeAttempts: 1})
  assert.equal(chosen, data)
})

test('unlabelled cards may expose safe checked states in the DOM', async () => {
  const page = fakePage({withDataSharing: true, unknownConsent: true})
  page.evaluate = async (_, label) => {
    const value = page.getConsent()
    if (label === 'Gather basic usage data') return value.usage
    if (label === 'Product updates') return value.product
    return null
  }
  const originalGetByText = page.getByText
  page.getByText = pattern => {
    const result = originalGetByText(pattern)
    result.click = async () => {
      const label = pattern.test('Gather basic usage data') ? 'usage' : 'product'
      page.actions.push('consent-card-click-' + label)
      page.setConsent(label, !page.getConsent()[label])
    }
    return result
  }
  await importDisposableWallet(page, mnemonic, password)
  assert.deepEqual(page.getConsent(), {usage: false, product: false})
  assert.ok(page.actions.includes('consent-card-click-usage'))
  assert.ok(page.actions.includes('consent-continue'))
})


// Reproduces the Windows screenshot where MetaMask finishes in a second tab:
// its first tab retains a *disabled* onboarding Done button indefinitely.
function visibleWalletHome(id, address, {visibleAddress = address, route = '/home'} = {}) {
  const abbreviation = `${visibleAddress.slice(0, 7)}...${visibleAddress.slice(-5)}`
  return {
    url: () => `chrome-extension://${id}/home.html#${route}`,
    isClosed: () => false,
    locator: selector => ({
      first() {return this},
      isVisible: async () => selector === '[data-testid="account-menu-icon"]',
    }),
    getByRole: () => ({first() {return this}, isVisible: async () => false}),
    getByText: regex => ({
      first() {return this},
      isVisible: async () => regex.test(abbreviation),
    }),
    async bringToFront() {},
    async waitForTimeout() {},
  }
}

test('stale disabled Done is bypassed when a second tab has wallet home after phrase import', async () => {
  const {verifyWalletHomeIdentity} = await import('./metamask_onboarding.mjs')
  const id = 'a'.repeat(32)
  const address = '0x0a90Dea6B7a35BfA52dDCe7bfF5a38038D91593B'
  const completing = fakePage({url: `chrome-extension://${id}/home.html#/onboarding/welcome`})
  const originalLocator = completing.locator
  completing.locator = selector => {
    const locator = originalLocator(selector)
    if (selector.includes('onboarding-complete-done')) {
      locator.isEnabled = async () => false
      locator.click = async () => {throw new Error('Disabled Done must not be clicked')}
    }
    return locator
  }
  const liveHome = visibleWalletHome(id, address)
  const context = {pages: () => completing.getState() === 'complete' ?
    [completing, liveHome] : [completing]}
  const result = await importDisposableWallet(completing, mnemonic, password, {context, extensionId: id})
  assert.equal(completing.getState(), 'complete')
  assert.equal(result, liveHome)
  assert.equal(await screenLabel(result), 'wallet-home')
  assert.equal(await verifyWalletHomeIdentity(result, address, id), true)
  assert.ok(!completing.actions.some(action => action.includes('onboarding-complete-done')))
})

test('a disabled completion button without any live wallet home refuses cleanly', async () => {
  const id = 'b'.repeat(32)
  const completing = fakePage({url: `chrome-extension://${id}/home.html#/onboarding/welcome`})
  const originalLocator = completing.locator
  completing.locator = selector => {
    const locator = originalLocator(selector)
    if (selector.includes('onboarding-complete-done')) {
      locator.isEnabled = async () => false
      locator.click = async () => {throw new Error('Disabled Done must not be clicked')}
    }
    return locator
  }
  const context = {pages: () => [completing]}
  await assert.rejects(
    importDisposableWallet(completing, mnemonic, password, {context, extensionId: id}),
    /MetaMask onboarding incomplete.*no wallet profile certified/
  )
  assert.ok(!completing.actions.some(action => action.includes('onboarding-complete-done')))
})

test('wallet-home address gate refuses a mismatched or unrecognized wallet', async () => {
  const {verifyWalletHomeIdentity} = await import('./metamask_onboarding.mjs')
  const id = 'c'.repeat(32)
  const expected = '0x0a90Dea6B7a35BfA52dDCe7bfF5a38038D91593B'
  const other = '0x1234567890123456789012345678901234567890'
  assert.equal(await verifyWalletHomeIdentity(visibleWalletHome(id, expected), expected, id), true)
  await assert.rejects(verifyWalletHomeIdentity(visibleWalletHome(id, other), expected, id),
    /displayed account does not match/)
  await assert.rejects(verifyWalletHomeIdentity(visibleWalletHome('d'.repeat(32), expected), expected, id),
    /verified extension wallet home unavailable/)
  await assert.rejects(verifyWalletHomeIdentity(visibleWalletHome(id, expected, {route: '/onboarding/welcome'}), expected, id),
    /verified extension wallet home unavailable/)
  await assert.rejects(verifyWalletHomeIdentity(visibleWalletHome(id, expected), 'invalid', id),
    /invalid expected disposable address/)
})

test('an unrelated existing wallet home does not bypass fresh import', async () => {
  const id = 'e'.repeat(32)
  const stale = fakePage({stuck: true, url: `chrome-extension://${id}/home.html#/onboarding/welcome`})
  const home = visibleWalletHome(id, '0x0a90Dea6B7a35BfA52dDCe7bfF5a38038D91593B')
  const context = {pages: () => [stale, home]}
  await assert.rejects(importDisposableWallet(stale, mnemonic, password,
    {context, extensionId: id, deadlineMs: 1000}), /no wallet profile certified/)
})

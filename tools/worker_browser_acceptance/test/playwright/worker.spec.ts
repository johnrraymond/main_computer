import { testWithSynpress } from '@synthetixio/synpress'
import { MetaMask, metaMaskFixtures } from '@synthetixio/synpress/playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import workerWallet from '../wallet-setup/worker.setup'

const test = testWithSynpress(metaMaskFixtures(workerWallet))
const { expect } = test

const TESTNET_CHAIN = '0x28757b1'
const FAIL_MARKER = /failed|error|rejected|wrong chain|unreachable/i

test('real MetaMask + Hub wallet acceptance', async ({ context, page, metamaskPage, extensionId }) => {
  const mode = process.env.MC_WORKER_TEST_MODE || 'wallet'
  if (mode !== 'wallet' && mode !== 'worker') throw new Error('Unsupported acceptance mode')
  const metamask = new MetaMask(context, metamaskPage, workerWallet.walletPassword, extensionId)
  const evidence: Record<string, unknown> = { schema: 'main-computer.worker-browser-acceptance.v1', mode, network: 'testnet', stages: [] }
  const stages = evidence.stages as string[]
  const reportDir = resolve(process.env.MC_WORKER_TEST_REPORT_DIR || 'test-results')
  mkdirSync(reportDir, { recursive: true })
  let startedWork = false
  let changedPaidPolicy = false
  const logs: string[] = []
  page.on('dialog', async dialog => {
    if (dialog.type() === 'confirm' && startedWork) await dialog.accept()
    else await dialog.dismiss()
  })
  // Event names only: do not capture signatures, signed requests, or auth tokens.
  page.on('console', msg => {
    const s = msg.text()
    if (s.includes('[worker-msk] signature.created')) logs.push('msk.signature.created')
    if (s.includes('[worker-msk] hub.response')) logs.push('msk.hub.response')
    if (s.includes('[worker-msk] request.failed')) logs.push('msk.request.failed')
    if (s.includes('connect.finalized.connected')) logs.push('wallet.connected')
  })
  try {
    await page.goto(process.env.MC_WORKER_TEST_URL || 'http://127.0.0.1:8765/applications/worker', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('#worker-app')).toBeVisible()
    await page.locator('#worker-auto-hub-testnet').check()
    await expect(page.locator('#worker-auto-hub-testnet')).toBeChecked()
    await expect(page.locator('#worker-network-chain-id')).toContainText('42424241')
    stages.push('testnet-hub-selected')

    // This is real MetaMask, not window.ethereum injected by the test.
    // The test wallet has already been configured on testnet during caching.
    const before = await page.evaluate(async () => {
      const provider = (window as any).ethereum
      if (!provider || !provider.isMetaMask) throw new Error('Real MetaMask provider missing')
      return { chainId: await provider.request({method: 'eth_chainId'}),
               blockNumber: await provider.request({method: 'eth_blockNumber'}) }
    })
    expect(before.chainId).toBe(TESTNET_CHAIN)
    expect(/^0x[0-9a-f]+$/i.test(before.blockNumber)).toBeTruthy()
    stages.push('metamask-rpc-proven')

    // The Worker must perform its OWN RPC proof before completing hydration.
    const previouslyApproved = await page.evaluate(async () => {
      const p = (window as any).ethereum
      return await p.request({ method: 'eth_accounts' }) as string[]
    })
    await page.locator('#worker-connect-wallet').click()
    if (!previouslyApproved.length) await metamask.connectToDapp()
    await expect(page.locator('#worker-primary-wallet-status')).toContainText('0x', { timeout: 60_000 })
    const wallet = await page.evaluate(async () => {
      const p = (window as any).ethereum
      const [account] = await p.request({method: 'eth_accounts'})
      return { account, chainId: await p.request({method: 'eth_chainId'}),
               blockNumber: await p.request({method: 'eth_blockNumber'}) }
    })
    expect(wallet.account).toMatch(/^0x[0-9a-f]{40}$/i)
    const shownWallet = ((await page.locator('#worker-primary-wallet-status').innerText()) || '').toLowerCase()
    expect(shownWallet).toContain(wallet.account.slice(0, 6).toLowerCase())
    expect(shownWallet).toContain(wallet.account.slice(-4).toLowerCase())
    expect(wallet.chainId).toBe(TESTNET_CHAIN)
    expect(wallet.blockNumber).toMatch(/^0x[0-9a-f]+$/i)
    stages.push('worker-wallet-connected-and-rpc-proven')
    evidence.walletAddress = wallet.account
    evidence.chainId = wallet.chainId

    // The wallet test exercises real key issuance. Registration mode may
    // reuse the key issued by an earlier wallet run. The Hub will validate
    // that key independently when accepting Worker registration.
    const state = (await page.locator('#worker-multisession-key-state').innerText()).trim()
    const alreadyActive = state === 'Active'
    if (alreadyActive && mode === 'wallet') {
      throw new Error('Test wallet already has an active MSK. Use a fresh disposable wallet for a new signing test; no automatic revocation.')
    }
    if (!alreadyActive) {
      await page.locator('#worker-request-multisession-key').click()
      // Wallet signs locally; Hub must independently verify the signature.
      await metamask.confirmSignature()
      await expect(page.locator('#worker-multisession-key-state')).toHaveText('Active', { timeout: 60_000 })
      if (!logs.includes('msk.signature.created')) throw new Error('Real Worker signature event was not observed')
      if (logs.includes('msk.request.failed')) throw new Error('Hub rejected multi-session key request')
      stages.push('hub-msk-issued')
    } else {
      stages.push('existing-msk-reused-for-registration')
    }
    const keyId = (await page.locator('#worker-multisession-key-id').innerText()).trim()
    if (!keyId || keyId === '—') throw new Error('No Hub-issued multi-session key ID is available')
    evidence.keyIssued = !alreadyActive
    evidence.keyId = keyId

    if (mode === 'worker') {
      // Optional, explicitly authorized mode: worker advertises compute.
      const enabled = page.locator('#worker-rental-enabled')
      if (!(await enabled.isChecked())) { await enabled.check(); changedPaidPolicy = true }
      await page.locator('#worker-network-ring').selectOption('3')
      await expect(page.locator('#worker-network-work-now')).toBeEnabled()
      await page.locator('#worker-network-work-now').click()
      await expect(page.locator('#worker-work-now-dialog')).toBeVisible()
      await page.locator('#worker-work-now-15').click()
      startedWork = true
      await expect(page.locator('#worker-network-hub-registration')).toHaveText('Accepted', {timeout: 45_000})
      const registered = (await page.locator('#worker-network-hub-registration').innerText()).trim()
      if (FAIL_MARKER.test(registered)) throw new Error('Hub registration failed: ' + registered)
      const workerId = (await page.locator('#worker-network-worker-id').innerText()).trim()
      if (!workerId || workerId === '—') throw new Error('No Hub worker identity was reported')
      evidence.workerId = workerId
      stages.push('hub-worker-registered')
      // This mode certifies registration, not paid execution or settlement.
    }
    evidence.ok = true
  } catch (error) {
    evidence.ok = false
    evidence.error = error instanceof Error ? error.message : String(error)
    await page.screenshot({path: resolve(reportDir, 'worker-failure.png')}).catch(() => undefined)
    throw error
  } finally {
    if (startedWork) {
      // The acceptance harness must not leave a work session running.
      try {
        await page.locator('#worker-network-work-now').click()
        await page.locator('#worker-work-now-finish').click()
        stages.push('work-session-finish-requested')
      } catch (error) {
        evidence.ok = false
        evidence.cleanupError = error instanceof Error ? error.message : String(error)
      }
    }
    if (changedPaidPolicy) {
      try { await page.locator('#worker-rental-enabled').uncheck() }
      catch (error) {
        evidence.ok = false
        evidence.cleanupError = error instanceof Error ? error.message : String(error)
      }
    }
    evidence.events = logs
    writeFileSync(resolve(reportDir, 'acceptance.json'), JSON.stringify(evidence, null, 2) + '\n', {mode: 0o600})
    if (evidence.cleanupError) throw new Error('Work session cleanup failed: ' + evidence.cleanupError)
  }
})

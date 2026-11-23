// A late MCEL seller read can rehydrate the form while the operator edits it.
// Retry ONLY before a consequent write is submitted, and insist that the
// native confirmation still carries the exact authorized rate/token budget.
// This helper invokes the ordinary form and confirmation; it is not an API
// shortcut, and it never relaxes the acceptance postcondition.
export async function saveSellerOfferAfterRefresh({
  expectedRate, expectedTokens, writeFields, readFields, submit,
  mutationSubmitted, delay, onSample = () => {}, onStaleConfirmation = () => {},
  attempts = 6,
} = {}) {
  if (!/^\d+(?:\.\d{1,18})?$/.test(String(expectedRate)) ||
      !Number.isSafeInteger(expectedTokens) || expectedTokens < 1 ||
      !Number.isInteger(attempts) || attempts < 1 || attempts > 8 ||
      [writeFields, readFields, submit, mutationSubmitted, delay].some(f => typeof f !== 'function')) {
    throw Error('OFFER_PRICE_REFRESH_RETRY_INVALID_CONTRACT')
  }
  let resets = 0, staleConfirmations = 0
  for (let attempt = 1; attempt <= attempts; attempt++) {
    if (mutationSubmitted()) throw Error('OFFER_PRICE_MUTATION_ALREADY_SUBMITTED')
    await writeFields({rate:expectedRate, tokens:expectedTokens})
    // Avoid an artificial delay between editing and clicking Save: a late
    // refresh can replace draft values during that delay. The first short
    // probe catches a pending hydration; subsequent retries fill, verify,
    // and submit promptly. The exact-value native confirmation remains the
    // final guard against an intervening refresh.
    if (attempt === 1) await delay(80)
    const observed = await readFields()
    const matched = observed?.rate === expectedRate && observed?.tokens === expectedTokens
    onSample({attempt, rate:observed?.rate, tokens:observed?.tokens, matched,
      resets, staleConfirmations})
    if (!matched) {
      resets++
      await delay(150)
      continue
    }
    try {
      await submit({attempt})
      return {attempts:attempt, resets, staleConfirmations}
    } catch (error) {
      // Only the exact known stale-form refusal may be retried, and only if
      // no backend mutation was sent. An unknown dialog, timeout, or HTTP
      // failure MUST NOT result in another consequential submission.
      if (error?.message !== 'Unexpected MCEL confirmation refused' || mutationSubmitted()) throw error
      staleConfirmations++
      onStaleConfirmation({attempt, resets, staleConfirmations})
      await delay(150)
    }
  }
  throw Error('OFFER_PRICE_FORM_REFRESH_DID_NOT_SETTLE')
}

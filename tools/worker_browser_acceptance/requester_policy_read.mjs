// Classify only the backend settings read response. Never accept UI defaults as
// proof of a server policy, and never persist the policy's values in evidence.
const own = (object, field) => Object.prototype.hasOwnProperty.call(object, field)
const make = (httpStatus, result) => ({
  ok: result === 'saved-policy' || result === 'unsaved-defaults' || result === 'partial-defaults',
  diagnostic: {version: 'requester-policy-read-v1', httpStatus, result},
})

export function classifyRequesterPolicyRead(reply) {
  const http = Number.isInteger(reply?.http) && reply.http >= 0 && reply.http <= 599
    ? reply.http : 0
  if (http !== 200) return make(http, http === 0 ? 'transport-failed' : 'http-rejected')
  if (reply?.body?.ok !== true) return make(http, 'backend-refused')
  const settings = reply.body.settings
  if (!settings || typeof settings !== 'object' || Array.isArray(settings))
    return make(http, 'invalid-settings-object')
  const enabled = own(settings, 'remoteEnabled')
  const budget = own(settings, 'remoteDailyLimit')
  if (enabled && typeof settings.remoteEnabled !== 'boolean')
    return make(http, 'invalid-enabled-type')
  if (budget && (!Number.isSafeInteger(settings.remoteDailyLimit) || settings.remoteDailyLimit < 0))
    return make(http, 'invalid-budget-type')
  // An absent persisted policy is legitimately represented by {}. The backend
  // treats missing values as disabled and zero spendable budget. Do NOT infer
  // the browser form's displayed defaults, and do NOT write settings here.
  if (!enabled && !budget) return make(http, 'unsaved-defaults')
  if (enabled && budget) return make(http, 'saved-policy')
  return make(http, 'partial-defaults')
}

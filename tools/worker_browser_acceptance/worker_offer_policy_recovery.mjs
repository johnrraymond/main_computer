// Crash-safe, test-only seller policy recovery. This receipt contains policy
// fields, never wallet credentials. It is authoritative only for an explicitly
// authorized native MCEL offer/work-cycle acceptance run.
import {existsSync, lstatSync, readFileSync, writeFileSync, unlinkSync} from 'node:fs'

export const POLICY_RECOVERY_SCHEMA='mcel.worker.offer-policy-recovery.v1'
export const POLICY_RECOVERY_TARGET='http://127.0.0.1:8765/applications/mcel-semantic-worker'

export function sellerPolicyReceipt(settings){
  if(!settings || typeof settings.sellerEnabled!=='boolean' ||
     !['totally_idle','ai_idle'].includes(settings.sellerAvailabilityMode))
    throw Error('OFFER_POLICY_BASELINE_UNVERIFIED')
  const receipt={schema:POLICY_RECOVERY_SCHEMA,target:POLICY_RECOVERY_TARGET,
    network:'testnet',sellerEnabled:settings.sellerEnabled,
    sellerAvailabilityMode:settings.sellerAvailabilityMode}
  const tokens=settings.sellerTargetTokens
  if(Number.isSafeInteger(tokens)&&tokens>0&&tokens<=1000000000)
    receipt.sellerTargetTokens=tokens
  if(settings.sellerCreditsPerToken!==undefined){
    const price=String(settings.sellerCreditsPerToken).trim()
    if(!/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,18})?$/.test(price) ||
       !/[1-9]/.test(price))throw Error('OFFER_POLICY_BASELINE_PRICE_UNVERIFIED')
    receipt.sellerCreditsPerToken=price
  }
  return Object.freeze(receipt)
}

export function parseSellerPolicyReceipt(value){
  if(!value || value.schema!==POLICY_RECOVERY_SCHEMA ||
     value.target!==POLICY_RECOVERY_TARGET || value.network!=='testnet' ||
     typeof value.sellerEnabled!=='boolean' ||
     !['totally_idle','ai_idle'].includes(value.sellerAvailabilityMode))
    throw Error('OFFER_POLICY_RECOVERY_RECEIPT_UNVERIFIED')
  if(value.sellerTargetTokens!==undefined &&
    (!Number.isSafeInteger(value.sellerTargetTokens) || value.sellerTargetTokens<1 || value.sellerTargetTokens>1000000000))
    throw Error('OFFER_POLICY_RECOVERY_TOKENS_UNVERIFIED')
  if(value.sellerCreditsPerToken!==undefined &&
     (typeof value.sellerCreditsPerToken!=='string' ||
      !/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,18})?$/.test(value.sellerCreditsPerToken) ||
      !/[1-9]/.test(value.sellerCreditsPerToken)))
    throw Error('OFFER_POLICY_RECOVERY_PRICE_UNVERIFIED')
  return sellerPolicyReceipt(value)
}

export function sellerPolicyMatches(observed, expected){
  return Boolean(observed && observed.sellerEnabled===expected.sellerEnabled &&
    observed.sellerAvailabilityMode===expected.sellerAvailabilityMode &&
    (expected.sellerTargetTokens===undefined ||
      observed.sellerTargetTokens===expected.sellerTargetTokens) &&
    (expected.sellerCreditsPerToken===undefined ||
      String(observed.sellerCreditsPerToken)===expected.sellerCreditsPerToken))
}

export function readSellerPolicyReceipt(path){
  if(!existsSync(path))return null
  if(!lstatSync(path).isFile())throw Error('OFFER_POLICY_RECOVERY_NOT_REGULAR_FILE')
  try{return parseSellerPolicyReceipt(JSON.parse(readFileSync(path,'utf8')))}
  catch{throw Error('OFFER_POLICY_RECOVERY_RECEIPT_UNVERIFIED')}
}

export function writeSellerPolicyReceipt(path, settings){
  const receipt=sellerPolicyReceipt(settings)
  if(existsSync(path)){
    const old=readSellerPolicyReceipt(path)
    if(!sellerPolicyMatches(old,receipt) ||
       (old.sellerTargetTokens!==undefined && receipt.sellerTargetTokens!==undefined &&
        old.sellerTargetTokens!==receipt.sellerTargetTokens))
      throw Error('OFFER_POLICY_PENDING_RECOVERY_CONFLICT')
    return receipt
  }
  writeFileSync(path,JSON.stringify(receipt)+'\n',{flag:'wx',mode:0o600})
  return receipt
}

export function clearSellerPolicyReceipt(path){
  if(existsSync(path))unlinkSync(path)
}

// `read` and `write` are injected so tests cannot touch a live server.
// Require stable backend observations, not merely an acknowledged POST.
export async function enforceSellerPolicy({expected,read,write,delay=async()=>{},attempts=3}){
  const baseline=parseSellerPolicyReceipt(expected)
  for(let attempt=1;attempt<=attempts;attempt++){
    try{
      if(!sellerPolicyMatches(await read(),baseline))await write(baseline)
      await delay(450)
      if(sellerPolicyMatches(await read(),baseline)){
        await delay(450)
        if(sellerPolicyMatches(await read(),baseline))return {verified:true,attempts:attempt}
      }
    }catch{
      // Retry only the specific bounded, authorized policy write/read.
    }
  }
  throw Error('OFFER_POLICY_BACKEND_RESTORE_UNVERIFIED')
}

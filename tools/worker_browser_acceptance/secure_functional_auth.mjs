// Fresh browser session, normal MC proof protocol; no credential persistence/export.
import {openSync,readSync,closeSync} from 'node:fs'
import {homedir} from 'node:os'
import {join} from 'node:path'
import {createHmac} from 'node:crypto'
export const ORIGIN='https://dev.main-computer.localhost:8875'
const AUTH='/api/auth/bootstrap/', COOKIE='__Host-MC-Session', IDENTITIES='/api/applications/slh/identities'
export function proofSignature(path,challenge){
  if(challenge?.algorithm!=='HMAC-SHA256'||![challenge.challenge_id,challenge.nonce].every(v=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(v)))throw Error('invalid-challenge')
  const fd=openSync(path,'r');let raw=Buffer.alloc(4097),n
  try{n=readSync(fd,raw,0,raw.length,null)}finally{closeSync(fd)}
  if(n>4096)throw Error('invalid-proof-size')
  const record=JSON.parse(raw.subarray(0,n).toString('utf8'));raw.fill(0)
  if(record?.type!=='mc-proof-v2'||typeof record.key_b64!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(record.key_b64))throw Error('invalid-proof')
  const key=Buffer.from(record.key_b64,'base64url');if(key.length!==32)throw Error('invalid-proof-length')
  try{return {challenge_id:challenge.challenge_id,signature:createHmac('sha256',key).update(`MC-AUTH-BOOTSTRAP-V1\n${challenge.challenge_id}\n${challenge.nonce}\n`,'ascii').digest('base64url')}}finally{key.fill(0)}
}
async function request(page,path,body=null){
  return page.evaluate(async ({path,body})=>{
    const opts={credentials:'same-origin',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(10000)}
    if(body!==null)Object.assign(opts,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    const r=await fetch(path,opts);return {http:r.status,data:r.ok?await r.json():null}
  },{path,body})
}
export async function authenticateContext(context){
  if((await context.cookies(ORIGIN)).some(c=>c.name===COOKIE))throw Error('context-not-disposable')
  const page=await context.newPage()
  try{
    await page.route('**/*',route=>{
      const req=route.request(),u=new URL(req.url());const method=req.method()
      const allowed=u.origin===ORIGIN&&!u.search&&!u.hash&&((method==='GET'&&[AUTH+'session',AUTH+'challenge',IDENTITIES].includes(u.pathname))||(method==='POST'&&u.pathname===AUTH+'verify'))
      return allowed?route.continue():route.abort()
    })
    const response=await page.goto(ORIGIN+AUTH+'session',{waitUntil:'domcontentloaded',timeout:10000})
    if(response?.status()!==200||page.url()!==ORIGIN+AUTH+'session')throw Error('trusted-https-failed')
    const denied=await request(page,IDENTITIES);if(![401,403].includes(denied.http))throw Error('anonymous-denied-failed')
    const challenge=await request(page,AUTH+'challenge');if(challenge.http!==200)throw Error('challenge-failed')
    const signed=proofSignature(process.env.MCEL_FUNCTIONAL_PROOF_FILE||join(homedir(),'AAA_AUTH_MAIN_COMPUTER.json'),challenge.data)
    const verified=await request(page,AUTH+'verify',signed)
    if(verified.http!==200||verified.data?.authenticated!==true)throw Error('authenticate-failed')
    const cookies=(await context.cookies(ORIGIN)).filter(c=>c.name===COOKIE)
    if(cookies.length!==1||!cookies[0].secure||!cookies[0].httpOnly||cookies[0].sameSite!=='Strict'||cookies[0].path!=='/')throw Error('secure-cookie-failed')
    const session=await request(page,AUTH+'session');if(session.http!==200||session.data?.authenticated!==true)throw Error('authenticated-session-failed')
  }finally{await page.close()}
}
export async function revokeContext(context){
  if(!(await context.cookies(ORIGIN)).some(c=>c.name===COOKIE))return false
  const page=await context.newPage()
  try{
    await page.route('**/*',route=>{
      const req=route.request(),u=new URL(req.url());const method=req.method()
      const allowed=u.origin===ORIGIN&&!u.search&&!u.hash&&((method==='GET'&&[AUTH+'session',IDENTITIES].includes(u.pathname))||(method==='POST'&&u.pathname===AUTH+'logout'))
      return allowed?route.continue():route.abort()
    })
    const response=await page.goto(ORIGIN+AUTH+'session',{waitUntil:'domcontentloaded',timeout:10000})
    if(response?.status()!==200||page.url()!==ORIGIN+AUTH+'session')return false
    const r=await request(page,AUTH+'logout',{})
    const denied=await request(page,IDENTITIES)
    return r.http===200&&[401,403].includes(denied.http)
  }finally{await page.close()}
}

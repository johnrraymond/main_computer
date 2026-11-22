import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {expectedMcelConfirmation,clickMcelConfirmed} from './mcel_live_confirmations.mjs'
const formTail='This action may stop a process or server. Continue?'
const actionTail='This may stop a process or server. Continue?'

function fake(pageMessage, dialogType='confirm'){
  const listeners=new Map(), events=[]
  const page={
    on(name,fn){listeners.set(name,fn)},
    off(name,fn){if(listeners.get(name)===fn)listeners.delete(name)},
  }
  const locator={click:async()=>{
    const handler=listeners.get('dialog')
    if(!handler)return
    await handler({type:()=>dialogType,message:()=>pageMessage,
      accept:async()=>events.push('accepted'),
      dismiss:async()=>events.push('dismissed')})
  }}
  return {page,locator,events,listeners}
}

test('ring confirmation matches typed MCEL form and exact ring',async()=>{
  const msg=`Confirm Select Destination?\nRequested Ring: 3\n${formTail}`
  assert.equal(expectedMcelConfirmation('select-destination','confirm',msg,{expectedRing:3}),true)
  assert.equal(expectedMcelConfirmation('select-destination','confirm',msg,{expectedRing:2}),false)
  const r=fake(msg)
  await clickMcelConfirmed(r.page,r.locator,'select-destination',{expectedRing:3})
  assert.deepEqual(r.events,['accepted'])
  assert.equal(r.listeners.has('dialog'),false)
})

test('unauthorized or unrelated dialog is dismissed',async()=>{
  const r=fake(`Confirm consequential operation: Delete Identity\n${formTail}`)
  await assert.rejects(clickMcelConfirmed(r.page,r.locator,'select-destination',{expectedRing:3}),/Unexpected MCEL confirmation refused/)
  assert.deepEqual(r.events,['dismissed'])
})

test('unexpected prompt and absent confirmation refuse certification',async()=>{
  const r=fake(`Confirm Select Destination?\nRequested Ring: 3\n${formTail}`,'prompt')
  await assert.rejects(clickMcelConfirmed(r.page,r.locator,'select-destination',{expectedRing:3}),/Unexpected MCEL confirmation refused/)
  const noDialog={on(){},off(){}}
  await assert.rejects(clickMcelConfirmed(noDialog,{click:async()=>{}},'select-destination',{expectedRing:3}),/Expected MCEL confirmation absent/)
})

test('all other authorized native MCEL form and action confirmations are individually scoped',async()=>{
  const forms={
    'save-requester':'Save Requester',
    'save-seller':'Save Seller',
    'save-policy':'Save Policy',
    'work-now':'Work Now',
  }
  for(const [intent,title] of Object.entries(forms)){
    const msg=`Confirm ${title}?\nExample: value\n${formTail}`
    assert.equal(expectedMcelConfirmation(intent,'confirm',msg),true,intent)
    assert.equal(expectedMcelConfirmation('select-destination','confirm',msg,{expectedRing:3}),false,intent)
  }
  for(const [intent,title] of [['register-offer','Register Offer'],['finish-work-now','Finish Work Now']]){
    const msg=`Confirm consequential operation: ${title}\n${actionTail}`
    assert.equal(expectedMcelConfirmation(intent,'confirm',msg),true,intent)
    assert.equal(expectedMcelConfirmation('register-offer','confirm',`Confirm consequential operation: ${title}\nTarget: admin\n${actionTail}`),false,intent)
  }
})


test('native consequential action confirmation uses its distinct warning and refuses form wording',async()=>{
  const msg=`Confirm consequential operation: Register Offer\n${actionTail}`
  const r=fake(msg)
  await clickMcelConfirmed(r.page,r.locator,'register-offer')
  assert.deepEqual(r.events,['accepted'])
  assert.equal(expectedMcelConfirmation('register-offer','confirm',
    `Confirm consequential operation: Register Offer\n${formTail}`),false)
  assert.equal(expectedMcelConfirmation('save-seller','confirm',
    `Confirm Save Seller?\nTarget: admin\n${actionTail}`),false)
  const wrong=fake(`Confirm consequential operation: Register Offer\n${formTail}`)
  await assert.rejects(clickMcelConfirmed(wrong.page,wrong.locator,'register-offer'),
    /Unexpected MCEL confirmation refused/)
  assert.deepEqual(wrong.events,['dismissed'])
})

test('confirmation warning matches actual native MCEL runtime branches',()=>{
  const source=readFileSync(new URL('../../main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js',import.meta.url),'utf8')
  assert.match(source,/Confirm consequential operation:/)
  assert.ok(source.includes('\\n'+actionTail),'native action confirmation changed')
  assert.ok(source.includes('\\n'+formTail),'native form confirmation changed')
})

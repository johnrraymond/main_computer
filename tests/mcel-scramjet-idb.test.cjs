'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.resolve(__dirname,'../docker/mcel-browser-scramjet/mcel-scramjet-db.js'),'utf8');
const names = ['config','cookies','redirectTrackers','referrerPolicies','publicSuffixList'];

function simulate(initial = null) {
  let stores = initial === null ? null : new Set(initial);
  let deletes = 0, opens = 0;
  const indexedDB = {
    open(name, version) {
      assert.equal(name,'$scramjet'); assert.equal(version,1);
      opens++;
      const request = {};
      queueMicrotask(() => {
        const isNew = stores === null;
        if (isNew) stores = new Set();
        const db = {
          objectStoreNames: {contains(name) {return stores.has(name);}, [Symbol.iterator]: function* () {yield* stores;}},
          createObjectStore(name) {stores.add(name);}, close() {},
        };
        request.result = db;
        if (isNew && request.onupgradeneeded) request.onupgradeneeded();
        request.onsuccess?.();
      });
      return request;
    },
    deleteDatabase(name) {
      assert.equal(name,'$scramjet');
      deletes++;
      const request = {};
      queueMicrotask(() => {stores = null;request.onsuccess?.()});
      return request;
    },
  };
  const log = [];
  const sandbox = {window:{},indexedDB,console:{info(...args) {log.push(args.join(' '));}},setTimeout,clearTimeout};
  vm.runInNewContext(code,sandbox,{filename:'mcel-scramjet-db.js'});
  return {run:() => sandbox.window.mcelPrepareScramjetDB(),
    getState: () => ({names:Array.from(stores||[]),deletes,opens,log})};
}

test('fresh DB has all five expected v1 stores before the worker starts',async()=>{
  const s=simulate(); await s.run();
  assert.deepEqual(s.getState().names,names);
  assert.equal(s.getState().deletes,0);
});

test('empty version-1 DB from worker-first startup is repaired once',async()=>{
  const s=simulate([]); await s.run();
  assert.deepEqual(s.getState().names,names);
  assert.equal(s.getState().deletes,1);
  assert.equal(s.getState().opens,2);
});

test('valid database is preserved with no changes',async()=>{
  const s=simulate(names); await s.run();
  assert.deepEqual(s.getState().names,names);
  assert.equal(s.getState().deletes,0);
});

test('partial database is rejected without deleting user data',async()=>{
  const s=simulate(['config','cookies']);
  await assert.rejects(s.run(),/existing stores preserved/);
  assert.deepEqual(s.getState().names,['config','cookies']);
  assert.equal(s.getState().deletes,0);
});

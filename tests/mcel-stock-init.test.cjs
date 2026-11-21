'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const source = path.resolve(__dirname,'../docker/mcel-browser-scramjet/patch-stock-init.mjs');

test('pinned stock frontend cannot register SW until awaited controller/database initialization',()=>{
  const d=fs.mkdtempSync(path.join(os.tmpdir(),'scramjet-stock-'));
  try {
    fs.mkdirSync(path.join(d,'mcel-docker'));fs.mkdirSync(path.join(d,'public'));
    fs.copyFileSync(source,path.join(d,'mcel-docker/patch-stock-init.mjs'));
    fs.writeFileSync(path.join(d,'public/index.js'), `const scramjet = {};
scramjet.init();
async function submit(){
  await registerSW();
}
`);
    fs.writeFileSync(path.join(d,'public/index.html'), '<html><script src="index.js" defer></script></html>');
    const run=spawnSync(process.execPath,[path.join(d,'mcel-docker/patch-stock-init.mjs')],{encoding:'utf8'});
    assert.equal(run.status,0,run.stderr);
    const js=fs.readFileSync(path.join(d,'public/index.js'),'utf8');
    const html=fs.readFileSync(path.join(d,'public/index.html'),'utf8');
    assert.match(js,/await window\.mcelPrepareScramjetDB\(\)/);
    assert.match(js,/await scramjet\.init\(\)/);
    assert.match(js,/await mcelScramjetReady;\s*await registerSW\(\);/);
    assert.ok(html.indexOf('mcel-scramjet-db.js') < html.indexOf('index.js'));
    assert.equal(spawnSync(process.execPath,['--check',path.join(d,'public/index.js')]).status,0);
    const again=spawnSync(process.execPath,[path.join(d,'mcel-docker/patch-stock-init.mjs')],{encoding:'utf8'});
    assert.notEqual(again.status,0,'must fail closed when source no longer matches pinned version');
  } finally {fs.rmSync(d,{recursive:true,force:true});}
});

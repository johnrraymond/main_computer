'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../docker/mcel-browser-scramjet-v2/server.mjs'),'utf8');
// Evaluate the ACTUAL host patterns shipped to Docker; do not duplicate regexes.
const start = source.indexOf('const smokeFixtureHost = ');
const end = source.indexOf('\nif (smoke)',start);
if (start < 0 || end < 0) throw Error('Wisp policy declarations missing');
const sourceSnippet = source.slice(start,end);
const policy=vm.runInNewContext(sourceSnippet + '\n({fixture:smokeFixtureHost,public:smokePublicHostPatterns})');
const allows = host=>[policy.fixture,...policy.public].some(p=>p.test(host));

test('public acceptance hosts are allowed through Wisp hostname allowlist',()=>{
 for(const host of ['example.org','youtube.com','www.youtube.com',
 'youtubei.googleapis.com','i.ytimg.com','fonts.gstatic.com','yt3.ggpht.com',
 'rr3---sn-abc.googlevideo.com','accounts.google.com'])
   assert.equal(allows(host),true,host);
});
test('fixture remains allowed but unrelated private endpoints remain excluded',()=>{
 assert.equal(allows('127.0.0.9'),true);
 for(const host of ['127.0.0.10','127.0.0.1','localhost','metadata.google.internal',
 '192.168.1.1','10.0.0.1','youtube.com.attacker.invalid','example.org.evil.net'])
   assert.equal(allows(host),false,host);
});
test('public and fixture policy is smoke-only; production does not enable loopback',()=>{
 const before=source.slice(0,source.indexOf('const app = express()'));
 assert.match(before,/if \(smoke\) \{[\s\S]*hostname_whitelist = \[smokeFixtureHost, \.\.\.smokePublicHostPatterns\]/);
 assert.match(before,/\} else \{\s*wisp\.options\.allow_loopback_ips = false/);
 assert.match(before,/wisp\.options\.allow_private_ips = false/);
});

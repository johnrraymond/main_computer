import http from 'node:http';
import path from 'node:path';
import {createRequire} from 'node:module';
import express from 'express';
import {scramjetPath} from '@mercuryworkshop/scramjet/path';
import {server as wisp} from '@mercuryworkshop/wisp-js/server';

const require = createRequire(import.meta.url);
const packageDir = spec => path.dirname(require.resolve(spec));
const smoke = process.env.MCEL_SCRAMJET_SMOKE === '1';
const expectedHost = smoke ? '127.0.0.2' : '127.0.0.3';
const hostPattern = new RegExp('^' + expectedHost.replaceAll('.', '\\.') + ':[0-9]{1,5}$');
// Smoke mode must support TWO distinct acceptance classes:
// (1) the in-container 127.0.0.9 fixture, and (2) the fixed public HTTPS
// cohort (example.org and YouTube). The former v2 smoke ONLY allowed the
// fixture, accidentally denying EVERY public TLS stream; both Epoxy and
// libcurl consequently reported TLS handshake failures.
//
// These patterns apply exclusively to the temporary smoke container.
// Production uses the independent public-only/private-network-deny rules.
const smokeFixtureHost = /^127\.0\.0\.9$/;
const smokePublicHostPatterns = [
  /^example\.org$/i,
  /(?:^|\.)youtube\.com$/i,
  /(?:^|\.)ytimg\.com$/i,
  /(?:^|\.)googlevideo\.com$/i,
  /(?:^|\.)googleapis\.com$/i,
  /(?:^|\.)google\.com$/i,
  /(?:^|\.)gstatic\.com$/i,
  /(?:^|\.)ggpht\.com$/i,
  /(?:^|\.)googleusercontent\.com$/i,
];
if (smoke) {
  wisp.options.allow_loopback_ips = true; // Needed ONLY for the 127.0.0.9 fixture.
  wisp.options.allow_private_ips = false;
  wisp.options.allow_udp_streams = false;
  wisp.options.hostname_whitelist = [smokeFixtureHost, ...smokePublicHostPatterns];
} else {
  wisp.options.allow_loopback_ips = false;
  wisp.options.allow_private_ips = false;
  wisp.options.allow_udp_streams = false;
  wisp.options.hostname_blacklist = [/^metadata\.google\.internal$/i];
}
const app = express();
app.disable('x-powered-by');
app.use((req, res, next) => {
  if (!hostPattern.test(req.headers.host || '')) {
    res.status(421).type('text/plain').end('Isolated browser Host required');return;
  }
  res.setHeader('Cross-Origin-Opener-Policy','same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy','require-corp');
  res.setHeader('Cache-Control','no-store');
  next();
});
app.get('/healthz', (_req,res) => res.json({ok:true,engine:'scramjet-v2',version:'2.0.67-alpha.2',controller:'0.0.14',
  smokeHostPolicy: smoke ? 'fixture-plus-public-acceptance' : 'production-private-deny',
  smokePublicTargets: smoke ? ['example.org','www.youtube.com'] : []}));
app.use('/scram/', express.static(scramjetPath));
app.use('/controller/', express.static(packageDir('@mercuryworkshop/scramjet-controller')));
app.use('/utils/', express.static(packageDir('@mercuryworkshop/scramjet-utils')));
app.use('/libcurl/', express.static(packageDir('@mercuryworkshop/libcurl-transport')));
app.use('/epoxy/', express.static(packageDir('@mercuryworkshop/epoxy-transport')));
app.use(express.static('public'));
app.get('/', (_req,res) => res.redirect(302,'/mcel-surface.html'));
const server = http.createServer(app);
server.on('upgrade', (req,socket,head) => {
  try {
    const requested = new URL(req.url || '/', 'http://placeholder').pathname;
    const host = req.headers.host || '';
    if (requested !== '/wisp/' || !hostPattern.test(host) ||
      req.headers.origin !== 'http://' + host) {socket.destroy();return;}
    req.url = requested;
    wisp.routeRequest(req,socket,head);
  } catch (_) {socket.destroy();}
});
if (smoke) {
  const {startFixture} = await import('./fixture.mjs');
  await startFixture();
}
server.listen(Number(process.env.PORT || 8080), '0.0.0.0', () => {
  console.log('MCEL_SCRAMJET_V2_READY', 'port=' + (process.env.PORT || 8080), 'mode=' + (smoke?'smoke':'production'));
});

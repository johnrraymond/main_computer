import fs from 'node:fs';
const name = new URL('../src/index.js', import.meta.url);
let s = fs.readFileSync(name,'utf8');
const marker = 'Object.assign(wisp.options, {';
if (!s.includes(marker)) throw new Error('Scramjet Wisp initialization changed; refusing to build');
s = s.replace(marker, String.raw`if (process.env.MCEL_SCRAMJET_SMOKE === '1') {
  // Fixture mode ONLY. Never use this allowlist for real browsing.
  wisp.options.allow_loopback_ips = true;
  wisp.options.hostname_whitelist = [/^127\.0\.0\.9$/];
} else {
  // Default deny both direct and DNS-resolved private/loopback destinations.
  wisp.options.allow_private_ips = false;
  wisp.options.allow_loopback_ips = false;
  wisp.options.allow_udp_streams = false;
}
${marker}`);
const requestMark = 'handler(req, res);';
if (!s.includes(requestMark)) throw Error('Scramjet request handler changed');
s = s.replace(requestMark, String.raw`if (process.env.MCEL_SCRAMJET_BROWSER === '1' && !/^127\.0\.0\.3:[0-9]{1,5}$/.test(req.headers.host || '')) {
                res.writeHead(421); res.end('Invalid isolated browser Host'); return;
              }
              ${requestMark}`);
const upgradeMark = 'if (req.url.endsWith("/wisp/")) wisp.routeRequest(req, socket, head);';
if (!s.includes(upgradeMark)) throw Error('Scramjet Wisp routing changed');
s = s.replace(upgradeMark, String.raw`if (process.env.MCEL_SCRAMJET_BROWSER === '1') {
                const host = req.headers.host || '';
                if (!/^127\.0\.0\.3:[0-9]{1,5}$/.test(host) || req.headers.origin !== 'http://' + host) {
                  socket.destroy(); return;
                }
              }
              ${upgradeMark}`);
if (!s.includes('host: "0.0.0.0"')) throw Error('Unexpected Scramjet HTTP listener');
fs.writeFileSync(name,s);

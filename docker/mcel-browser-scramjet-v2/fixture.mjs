import {createServer} from 'node:http';
const content = {
  '/': ['text/html', `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/fixture.css"><script src="/fixture.js" defer></script></head><body><main id="fixture-root"><h1>MCEL Docker Browser Smoke</h1><p id="select">native selectable fixture text</p><input id="typing" aria-label="Type here"><button id="click">Click here</button><div id="clicked">not clicked</div><div id="data">not fetched</div><div id="fixture-ready">not ready</div><div id="color">css active</div><a id="next" href="/next">Next page</a></main></body></html>`],
  '/fixture.js': ['application/javascript', `window.__mcelFixtureScript = true;document.getElementById('click').addEventListener('click',()=>document.getElementById('clicked').textContent='clicked natively');fetch('/fixture-data.json').then(r=>r.json()).then(v=>{document.getElementById('data').textContent=v.value;document.getElementById('fixture-ready').textContent='ready'}).catch(e=>document.getElementById('fixture-ready').textContent='fetch failed: '+e);`],
  '/fixture-data.json': ['application/json', '{"value":"rewritten fetch succeeded"}'],
  '/fixture.css': ['text/css', '#color { color: rgb(17, 101, 207); }'],
  '/next': ['text/html', '<!doctype html><html><body><h1 id="next-title">Native navigation succeeded</h1></body></html>']
};
export async function startFixture() {
  const server = createServer((req,res)=>{
    if ((req.url || '').split('?')[0] === '/redirect-301') {
      res.writeHead(301, {'Location':'http://127.0.0.9:48083/redirected?source=301',
                          'Cache-Control':'no-store'});
      res.end();
      return;
    }
    if ((req.url || '').split('?')[0] === '/redirect-relative') {
      res.writeHead(302, {'Location':'/next', 'Cache-Control':'no-store'});
      res.end();
      return;
    }

    const [type, data] = content[(req.url || '').split('?')[0]] || [];
    if (!type) {res.writeHead(404);res.end('not found');return;}
    res.writeHead(200, {'Content-Type':type,'Cache-Control':'no-store'}); res.end(data);
  });
  await new Promise((resolve,reject)=>server.once('error',reject).listen(48081,'127.0.0.9',resolve));
  // Second listener exercises a real HTTP 301 between distinct origins/ports
  // without weakening Wisp's smoke-only exact-hostname allowlist.
  const redirected = createServer((req,res)=>{
    if (!req.url?.startsWith('/redirected?source=301')) {res.writeHead(404);res.end('not found');return;}
    res.writeHead(200,{'Content-Type':'text/html','Cache-Control':'no-store'});
    res.end('<!doctype html><html><body><h1 id="redirected-title">HTTP 301 target loaded</h1></body></html>');
  });
  await new Promise((resolve,reject)=>redirected.once('error',reject).listen(48083,'127.0.0.9',resolve));
  const trap = createServer((req,res)=>{res.writeHead(200,{'Content-Type':'text/plain'});res.end('MCEL_TRAP_SECRET_DENY');});
  await new Promise((resolve,reject)=>trap.once('error',reject).listen(48082,'127.0.0.10',resolve));
  console.log('MCEL_FIXTURE_READY http://127.0.0.9:48081/ and private trap 127.0.0.10:48082');
  return {server,redirected,trap};
}

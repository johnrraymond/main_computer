// Scramjet v2 worker. Preserve upstream routing; normalize ONLY successfully
// rewritten navigational documents for the isolated COEP=require-corp shell.
importScripts('/controller/controller.sw.js');

const MCEL_V2_PROXY_PATH = /^\/\~\/sj\//;
self.addEventListener('fetch', event => {
  if (!$scramjetController.shouldRoute(event)) return;
  if (event.request.mode !== 'navigate' ||
      !MCEL_V2_PROXY_PATH.test(new URL(event.request.url).pathname)) {
    event.respondWith($scramjetController.route(event));
    return;
  }
  event.respondWith((async () => {
    const response = await $scramjetController.route(event);
    if (!response || response.type === 'opaque' || response.type === 'opaqueredirect' ||
        response.status < 200 || response.status >= 300 || response.bodyUsed) return response;
    const headers = new Headers(response.headers);
    headers.set('Cross-Origin-Embedder-Policy', 'require-corp');
    headers.set('Cross-Origin-Resource-Policy', 'same-origin');
    headers.set('Cross-Origin-Opener-Policy', 'same-origin');
    return new Response(response.body, {
      status: response.status, statusText: response.statusText, headers,
    });
  })());
});
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

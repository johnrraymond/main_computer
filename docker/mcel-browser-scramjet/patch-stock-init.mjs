import fs from 'node:fs';
const js = new URL('../public/index.js', import.meta.url);
const html = new URL('../public/index.html', import.meta.url);
let code = fs.readFileSync(js, 'utf8');
let markup = fs.readFileSync(html, 'utf8');
if (code.includes('mcelScramjetReady') || markup.includes('/mcel-scramjet-db.js'))
  throw new Error('Scramjet stock frontend already patched; refusing duplicate transformation');
const beforeInit = 'scramjet.init();';
const beforeWorker = 'await registerSW();';
const beforeScript = '<script src="index.js" defer></script>';
for (const [source, marker] of [[code,beforeInit],[code,beforeWorker],[markup,beforeScript]]) {
  if (source.split(marker).length !== 2)
    throw new Error('Pinned Scramjet-App frontend changed; refusing unsafe startup rewrite: '+marker);
}
// Startup is ordered: complete version-1 IDB schema, await controller.init,
// only then allow the stock Scramjet frontend to register its worker.
code = code.replace(beforeInit,
  'const mcelScramjetReady = (async () => {\n\tawait window.mcelPrepareScramjetDB();\n\tawait scramjet.init();\n})();');
code = code.replace(beforeWorker, 'await mcelScramjetReady;\n\t\t' + beforeWorker);
markup = markup.replace(beforeScript,
  '<script src="/mcel-scramjet-db.js" defer></script>\n\t\t' + beforeScript);
fs.writeFileSync(js,code);
fs.writeFileSync(html,markup);

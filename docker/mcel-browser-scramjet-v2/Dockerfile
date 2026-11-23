FROM node:22-bookworm-slim
ENV NODE_ENV=production PORT=8080 MCEL_SCRAMJET_SMOKE=0
WORKDIR /srv/mcel-scramjet-v2
COPY package.json ./package.json
# All direct packages are pinned to the mutually compatible documented v2 row.
# Do not install Scramjet v1 or bare-mux in this image.
RUN npm install --omit=dev --no-audit --no-fund \
 && node --input-type=module -e 'import {scramjetPath} from "@mercuryworkshop/scramjet/path"; import {existsSync,readFileSync} from "node:fs"; const version=x=>JSON.parse(readFileSync(`node_modules/@mercuryworkshop/${x}/package.json`)).version; if(version("scramjet")!=="2.0.67-alpha.2"||version("scramjet-controller")!=="0.0.14") process.exit(4); if (!existsSync(scramjetPath + "/scramjet.js") || !existsSync(scramjetPath + "/scramjet.wasm")) process.exit(3)'
COPY server.mjs ./server.mjs
COPY fixture.mjs ./fixture.mjs
COPY mcel-surface.html sw.js ./public/
COPY mcel-surface.js ./public/mcel-surface.js
USER node
EXPOSE 8080
ENTRYPOINT ["node","./server.mjs"]

FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PORT=8080 \
    MCEL_SCRAMJET_SMOKE=1
WORKDIR /srv/scramjet
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN corepack pnpm install --prod --frozen-lockfile
COPY src/ ./src/
COPY public/ ./public/
COPY mcel-docker/patch-server.mjs ./mcel-docker/patch-server.mjs
COPY mcel-docker/fixture.mjs ./mcel-docker/fixture.mjs
COPY mcel-docker/entrypoint.mjs ./mcel-docker/entrypoint.mjs
RUN node ./mcel-docker/patch-server.mjs
COPY mcel-docker/patch-stock-init.mjs ./mcel-docker/patch-stock-init.mjs
COPY mcel-docker/mcel-scramjet-db.js ./public/mcel-scramjet-db.js
RUN node ./mcel-docker/patch-stock-init.mjs
COPY mcel-docker/mcel-surface.html ./public/mcel-surface.html
COPY mcel-docker/mcel-surface.js ./public/mcel-surface.js
COPY mcel-docker/mcel-sw-probe.js ./public/mcel-sw-probe.js
USER node
EXPOSE 8080
ENTRYPOINT ["node", "./mcel-docker/entrypoint.mjs"]

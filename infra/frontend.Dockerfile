ARG NODE_IMAGE=node:24-bookworm-slim
ARG CADDY_IMAGE=caddy:2.11.7-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM ${CADDY_IMAGE}
COPY --chmod=0644 infra/Caddyfile /etc/caddy/Caddyfile
# Port 8080 needs no file capabilities under cap_drop ALL/no-new-privileges.
RUN setcap -r /usr/bin/caddy && test -z "$(getcap /usr/bin/caddy)"
ENV HOME=/tmp XDG_CONFIG_HOME=/tmp/caddy/config XDG_DATA_HOME=/tmp/caddy/data
COPY --chmod=0555 infra/frontend-entrypoint.sh /usr/local/bin/mineagent-frontend
COPY --from=build /build/dist /srv
COPY frontend/FASTAPI-TEMPLATE-LICENSE frontend/LIGHTWEIGHT-CHARTS-NOTICE frontend/THIRD_PARTY_NOTICES.md /srv/notices/
RUN chmod -R a+rX /srv
USER 65532:65532
RUN test -r /srv/index.html && test -r /etc/caddy/Caddyfile \
    && caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
ENTRYPOINT ["/usr/local/bin/mineagent-frontend"]

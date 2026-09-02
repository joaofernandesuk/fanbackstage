# syntax=docker/dockerfile:1.7
FROM node:22.18.0-bookworm-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate

FROM base AS dependencies
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile

FROM dependencies AS build
ARG FANBACKSTAGE_ENVIRONMENT=production
ARG NEXT_PUBLIC_FANBACKSTAGE_API_URL
ARG NEXT_PUBLIC_FANBACKSTAGE_WEB_ORIGIN
ENV FANBACKSTAGE_ENVIRONMENT=$FANBACKSTAGE_ENVIRONMENT
ENV NEXT_PUBLIC_FANBACKSTAGE_API_URL=$NEXT_PUBLIC_FANBACKSTAGE_API_URL
ENV NEXT_PUBLIC_FANBACKSTAGE_WEB_ORIGIN=$NEXT_PUBLIC_FANBACKSTAGE_WEB_ORIGIN
COPY . .
RUN pnpm build

FROM node:22.18.0-bookworm-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app
RUN groupadd --system --gid 10001 fanbackstage \
    && useradd --system --uid 10001 --gid fanbackstage fanbackstage
COPY --from=build --chown=fanbackstage:fanbackstage /app/public ./public
COPY --from=build --chown=fanbackstage:fanbackstage /app/.next/standalone ./
# Next 16's standalone trace currently retains pnpm virtual-store links for
# transitive SWC helpers. Preserve the deterministic installed tree so those
# links resolve; no source, cache, browser binary, or package-manager store is copied.
COPY --from=build --chown=fanbackstage:fanbackstage /app/node_modules ./node_modules
COPY --from=build --chown=fanbackstage:fanbackstage /app/.next/static ./.next/static
USER fanbackstage
EXPOSE 3000
ENV HOSTNAME=0.0.0.0 PORT=3000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["node", "-e", "fetch('http://127.0.0.1:3000').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]

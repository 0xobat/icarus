# syntax=docker/dockerfile:1.7
# solana-executor — Execution cluster, Solana chain
# Build context: solana-executor/

FROM node:22-alpine AS base
WORKDIR /app
RUN corepack enable

FROM base AS deps
COPY package.json pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --prod=false

FROM deps AS prod-build
COPY tsconfig.json ./
COPY src ./src
RUN pnpm run build && pnpm prune --prod

FROM base AS prod
ENV NODE_ENV=production
COPY --from=prod-build /app/node_modules ./node_modules
COPY --from=prod-build /app/dist ./dist
COPY package.json ./
CMD ["node", "dist/main.js"]

FROM deps AS dev
ENV NODE_ENV=development
COPY tsconfig.json ./
COPY src ./src
CMD ["pnpm", "run", "dev"]

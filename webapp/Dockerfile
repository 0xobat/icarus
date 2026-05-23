# syntax=docker/dockerfile:1.7
# webapp — Curation cluster, Next.js read-only insight sidecar
# Build context: webapp/

FROM node:22-alpine AS base
WORKDIR /app
RUN corepack enable

FROM base AS deps
COPY package.json ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --prod=false

FROM deps AS prod-build
COPY tsconfig.json next.config.ts ./
COPY src ./src
RUN pnpm run build

FROM base AS prod
ENV NODE_ENV=production
COPY --from=prod-build /app/.next/standalone ./
COPY --from=prod-build /app/.next/static ./.next/static
COPY --from=prod-build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]

FROM deps AS dev
ENV NODE_ENV=development
COPY tsconfig.json next.config.ts ./
COPY src ./src
EXPOSE 3000
CMD ["pnpm", "run", "dev"]

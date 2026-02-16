FROM node:22-alpine AS base
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /app

# Install dependencies
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Copy source
COPY tsconfig.json ./
COPY src/ src/

# Build
RUN pnpm build

# ── Development target (hot-reload via tsx watch) ──
FROM base AS dev
COPY --from=base /app /app
CMD ["pnpm", "dev"]

# ── Production target ──
FROM node:22-alpine AS prod
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --prod
COPY --from=base /app/dist ./dist
CMD ["node", "dist/index.js"]

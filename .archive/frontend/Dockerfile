FROM node:22-alpine AS base
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /app

# Install dependencies
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# Copy source
COPY . .

# ── Dev target (hot-reload) ──
FROM base AS dev
ENV NODE_ENV=development
EXPOSE 3000
CMD ["pnpm", "dev"]

FROM node:22-slim AS base
RUN corepack enable
WORKDIR /app

# Install dependencies
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/package.json
RUN pnpm install --frozen-lockfile

# Copy source and build
COPY apps/web ./apps/web
# Ensure public directory exists for COPY in runner stage
RUN mkdir -p /app/apps/web/public
WORKDIR /app/apps/web
# Next.js production standalone rewrites 在 next build 时求值 process.env 并固化到产物
# (runtime env 不再生效)。须在 build 前注入容器服务名,否则 rewrite 固化为默认
# localhost:8000 → 容器内 ECONNREFUSED。API_URL 不带 /v1:gateway 路由裸路径。
ARG API_URL=http://localhost:8000
ARG ORCHESTRATOR_WS_URL=http://localhost:8000
ENV API_URL=$API_URL
ENV ORCHESTRATOR_WS_URL=$ORCHESTRATOR_WS_URL
RUN pnpm build

# Production image
FROM node:22-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Copy standalone output
COPY --from=base --chown=nextjs:nodejs /app/apps/web/.next/standalone ./
COPY --from=base --chown=nextjs:nodejs /app/apps/web/.next/static ./apps/web/.next/static
# Copy public directory (always exists thanks to mkdir in base stage)
COPY --from=base --chown=nextjs:nodejs /app/apps/web/public ./public

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "apps/web/server.js"]

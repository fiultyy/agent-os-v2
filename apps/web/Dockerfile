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

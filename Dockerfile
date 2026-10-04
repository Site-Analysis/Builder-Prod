# ── Stage 1: Build Next.js ────────────────────────────────────────────────────
FROM node:20-slim AS node-builder
WORKDIR /build

COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/
RUN npm ci

ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
ARG NEXT_PUBLIC_ENABLE_CADASTRAL_EXPLORER=1
# Address the BROWSER uses for the cadastral API (baked into the bundle). localhost only
# works when the browser runs on the same machine; behind Caddy use the public path,
# e.g. --build-arg NEXT_PUBLIC_CADASTRAL_API_URL=https://uat.qnit.in/api
ARG NEXT_PUBLIC_CADASTRAL_API_URL=http://localhost:8011

ENV NEXT_PUBLIC_CADASTRAL_API_URL=$NEXT_PUBLIC_CADASTRAL_API_URL
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL
ENV NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_ENABLE_CADASTRAL_EXPLORER=$NEXT_PUBLIC_ENABLE_CADASTRAL_EXPLORER

COPY apps/web/ apps/web/
RUN npm run build --workspace=apps/web

# ── Stage 2: Python 3.12 + Node 20 + supervisord ─────────────────────────────
FROM python:3.12-slim

# commit the image was built from: docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD)
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA       org.opencontainers.image.source="https://github.com/Site-Analysis/Builder-Prod"       org.opencontainers.image.title="builder-server"
ENV GIT_SHA=$GIT_SHA

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg supervisor && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Cadastral FastAPI service
WORKDIR /app/cadastral
COPY services/cadastral/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/cadastral/ .

# Next.js standalone output
WORKDIR /app/web
COPY --from=node-builder /build/apps/web/.next/standalone ./
COPY --from=node-builder /build/apps/web/.next/static ./apps/web/.next/static/

COPY supervisord.conf /app/supervisord.conf

EXPOSE 3000 8011

ENV HOSTNAME=0.0.0.0
ENV PORT=3000
ENV NODE_ENV=production

HEALTHCHECK --interval=30s --timeout=10s --start-period=1200s --retries=3 \
    CMD curl -f http://localhost:8011/health || exit 1

CMD ["supervisord", "-c", "/app/supervisord.conf"]

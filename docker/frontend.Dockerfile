# Frontend Dockerfile for DES Formulation System
# Multi-stage build: Node.js build -> Nginx serve

# Allow mirror registries to override the default Docker Hub images when needed.
ARG NODE_IMAGE=docker.io/library/node:20-alpine
ARG NGINX_IMAGE=docker.io/library/nginx:alpine

# ============ Build Stage ============
FROM ${NODE_IMAGE} AS builder

WORKDIR /app

# Copy package files
COPY src/web_frontend/package*.json ./

# Allow an alternate npm registry in restricted network environments.
ARG NPM_REGISTRY=

# Install dependencies (including devDependencies for build)
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi && npm ci

# Copy source code
COPY src/web_frontend/ ./

# Build argument for API URL
# Default to empty string for nginx proxy mode (production deployment)
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# Build frontend
RUN npm run build

# ============ Production Stage ============
FROM ${NGINX_IMAGE}

# Copy built files from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

# Set worker processes to 4 (default is auto, which may be too high)
# Use .* to match any number of spaces
RUN sed -i 's/worker_processes.*auto;/worker_processes 4;/' /etc/nginx/nginx.conf

# Expose port
EXPOSE 15080

# Health check (use IPv4 address to avoid IPv6 connection issues)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1

# Start nginx
CMD ["nginx", "-g", "daemon off;"]

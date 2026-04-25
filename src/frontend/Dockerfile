# Multi-stage build for React frontend

# Stage 1: Build
FROM node:20-alpine as builder

WORKDIR /build

# Accept build arguments
ARG VITE_API_BASE_URL=
ARG SSO_LOGIN_URL=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV SSO_LOGIN_URL=$SSO_LOGIN_URL

# Copy package files
COPY package*.json ./

# Install dependencies (including dev dependencies for build)
RUN npm ci

# Copy source code
COPY . .

# Build production bundle
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Runtime port for nginx → backend proxy (overridden via docker-compose environment)
ENV BACKEND_PORT=3001

# Copy base nginx config (includes conf.d/)
COPY nginx.conf /etc/nginx/nginx.conf

# Copy server block template (rendered at startup via entrypoint)
COPY default.conf.template /etc/nginx/templates/default.conf.template

# Copy built assets from builder
COPY --from=builder /build/dist /usr/share/nginx/html

# Health check
HEALTHCHECK --interval=30s --timeout=3s \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:80/ || exit 1

# Expose port
EXPOSE 80

# Render template with envsubst (only ${BACKEND_PORT} is substituted; nginx $vars are left intact)
CMD ["/bin/sh", "-c", "envsubst '$BACKEND_PORT' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]

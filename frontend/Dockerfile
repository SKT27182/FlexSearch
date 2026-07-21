# --- Stage 1: Build ---
FROM node:24.13.0-alpine3.23 AS builder
RUN npm install --global pnpm@11.15.1
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm run build

# --- Stage 2: Serve with Nginx ---
FROM nginx:1.29.1-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

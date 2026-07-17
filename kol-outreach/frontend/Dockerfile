# 构建阶段
# Node 18 满足 Vite 5 的运行要求，并兼容 NAS 预置镜像。
FROM node:18-alpine AS build
WORKDIR /app
ARG VITE_SNOV_INBOX_URL=""
ENV VITE_SNOV_INBOX_URL=${VITE_SNOV_INBOX_URL}
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

# 运行阶段(Caddy 静态托管 + API 反代)
FROM caddy:2-alpine
COPY --from=build /app/dist /usr/share/caddy
COPY Caddyfile /etc/caddy/Caddyfile
EXPOSE 80 443
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

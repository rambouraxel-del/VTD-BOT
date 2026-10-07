# 1) Build de la PWA en mode API (même domaine que l'API : VITE_API_URL vide).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ENV VITE_DATA_SOURCE=api VITE_API_URL=
RUN npm run build

# 2) Caddy sert la PWA et fait le reverse proxy HTTPS vers le pont API.
FROM caddy:2-alpine
COPY --from=build /app/dist /srv

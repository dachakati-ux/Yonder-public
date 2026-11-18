# Yonder Console — production Next.js image for Compose.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8080
ENV NEXT_PUBLIC_YONDER_GATEWAY_URL=$NEXT_PUBLIC_YONDER_GATEWAY_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ARG NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8080
ENV NEXT_PUBLIC_YONDER_GATEWAY_URL=$NEXT_PUBLIC_YONDER_GATEWAY_URL
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/public ./public
EXPOSE 3000
# Bind all interfaces inside the container; host publish stays on 127.0.0.1.
CMD ["npm", "run", "start", "--", "-H", "0.0.0.0", "-p", "3000"]

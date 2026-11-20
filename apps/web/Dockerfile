FROM node:22-alpine AS deps
WORKDIR /repo
COPY package.json ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm install --workspace apps/web

FROM node:22-alpine AS build
WORKDIR /repo
COPY --from=deps /repo/node_modules ./node_modules
COPY package.json ./
COPY apps/web ./apps/web
WORKDIR /repo/apps/web
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]


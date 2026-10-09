FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
ARG RESEARCHFORGE_BACKEND_URL=http://api:8001
ENV RESEARCHFORGE_BACKEND_URL=$RESEARCHFORGE_BACKEND_URL
RUN npm run build
FROM node:22-alpine
WORKDIR /app
RUN rm -rf /usr/local/lib/node_modules/npm /opt/yarn-* \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/yarn /usr/local/bin/yarnpkg
ENV NODE_ENV=production PORT=3010 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/index.html /app/app.js /app/operations.js /app/styles.css ./
USER node
EXPOSE 3010
CMD ["node", "server.js"]

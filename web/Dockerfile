FROM node:24-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
RUN npm run build

FROM nginx:stable-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/19-check-backend.sh /docker-entrypoint.d/19-check-backend.sh
RUN chmod 755 /docker-entrypoint.d/19-check-backend.sh

EXPOSE 8080

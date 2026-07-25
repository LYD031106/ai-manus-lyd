# Use Node.js as base image
FROM node:18-alpine as build-stage

# Set working directory
WORKDIR /app

# Keep builds safe on small production hosts.
ENV NODE_OPTIONS=--max-old-space-size=768

# Copy package.json and package-lock.json files
COPY package*.json ./

# Install project dependencies
RUN npm config set registry https://registry.npmmirror.com \
    && npm ci --no-audit --no-fund

# Copy project files
COPY . .

# Build application - skip type checking
RUN npm run build

# Use nginx as production environment image
FROM nginx:stable-alpine as production-stage

# Copy built files to nginx
COPY --from=build-stage /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf.template

# Copy entrypoint script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//' /docker-entrypoint.sh \
    && chmod +x /docker-entrypoint.sh

# Expose port 80
EXPOSE 80

# Start nginx with environment variable substitution
CMD ["/docker-entrypoint.sh"]

FROM nginx:alpine

# Copy the production build
COPY dist/ /usr/share/nginx/html/

# Replace default nginx config with SPA-routing config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Cloud Run routes to port 8080
EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]

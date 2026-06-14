FROM node:20-slim
WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
EXPOSE 5173
# Vite dev server; BACKEND_URL points the /api proxy at the backend service.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

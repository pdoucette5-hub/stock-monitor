# Stage 1: Build the React frontend
FROM node:20 AS frontend-build
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Stage 2: Set up the Python backend
FROM python:3.11-slim
WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend and app data/config needed at runtime
COPY backend/ ./backend/
COPY config/ ./config/
COPY cache/ ./cache/

# Copy the built React files into the backend's static directory
COPY --from=frontend-build /app/frontend/dist ./backend/static

# Expose the port
EXPOSE 8000

# Start the server
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
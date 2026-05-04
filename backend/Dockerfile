FROM python:3.12-slim

WORKDIR /app

# Install dependencies before copying app code so this layer is cached
# when only source files change.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the full backend package.
# backend/chroma_db/ (pre-built vector store) is included here.
COPY backend/ ./backend/

EXPOSE 8080

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]

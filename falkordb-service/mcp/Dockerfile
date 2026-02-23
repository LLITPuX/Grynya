FROM python:3.11-slim

WORKDIR /app

# Upgrade pip and install standard build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*
    
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# We override the main file mapping from compose, but we copy it just in case
COPY main.py .
COPY models.py .

# Expose HTTP port for API/SSE and health checks
EXPOSE 8000

# Start Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

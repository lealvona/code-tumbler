FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for psycopg2 and psutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY server.py .
COPY config.yaml.example config.yaml.example
RUN cp config.yaml.example config.yaml
COPY src/ src/
COPY prompts/ prompts/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

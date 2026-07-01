# BUILD & PUSH (from Mac, targeting linux/amd64):
#   docker buildx build --platform linux/amd64 -t ghcr.io/isaac-doe/isaac-ai-ready-record:latest --push .
FROM python:3.11-slim

WORKDIR /app

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

# Install curl for health checks
RUN apt-get update && apt-get install -y curl git && rm -rf /var/lib/apt/lists/*

# Install dependencies first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY portal/ portal/
COPY data/ data/
COPY schema/ schema/
COPY examples/ examples/
COPY tools/ tools/
# Gunicorn tuning (multi-worker/threaded) — auto-loaded from WORKDIR /app so it
# applies to `gunicorn portal.api:app` however the API container is launched.
COPY gunicorn.conf.py .

# Set ownership
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose Streamlit port and Flask API sidecar port
EXPOSE 8501
EXPOSE 8502

# Health check using Streamlit's built-in health endpoint
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Run Streamlit (default)
# To run the Flask API sidecar instead or alongside (gunicorn auto-loads
# /app/gunicorn.conf.py for multi-worker/threaded concurrency):
#   As a sidecar container:  CMD ["gunicorn", "-b", "0.0.0.0:8502", "portal.api:app"]
#   Or directly:             CMD ["python", "portal/api.py"]
#   Both processes together: use the start.sh entrypoint below
CMD ["streamlit", "run", "portal/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]

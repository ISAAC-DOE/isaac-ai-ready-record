#!/bin/bash
# start.sh - Run both Streamlit and the Flask API sidecar in a single container.
#
# Usage in Dockerfile:
#   COPY start.sh .
#   RUN chmod +x start.sh
#   CMD ["./start.sh"]
#
# Or override at deploy time:
#   docker run ... ./start.sh
#
# In Kubernetes, prefer running as two separate containers in the same pod
# (true sidecar pattern) rather than using this script. This script is
# provided for local development convenience.

set -e

echo "Starting ISAAC Portal API on port ${PORT:-8502}..."
gunicorn -c /app/gunicorn.conf.py -b 0.0.0.0:${PORT:-8502} portal.api:app &
API_PID=$!

echo "Starting Streamlit on port 8501..."
streamlit run portal/app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true &
STREAMLIT_PID=$!

# Wait for either process to exit, then kill the other
wait -n $API_PID $STREAMLIT_PID
echo "A process exited, shutting down..."
kill $API_PID $STREAMLIT_PID 2>/dev/null || true
wait

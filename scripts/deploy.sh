#!/usr/bin/env bash
# Code Tumbler — Build & deploy the full stack.
# Usage:  ./scripts/deploy.sh [--pull]
#   --pull   Pull latest images before building (default: local build only)

set -euo pipefail
cd "$(dirname "$0")/.."

PULL_FLAG=""
if [[ "${1:-}" == "--pull" ]]; then
  PULL_FLAG="--pull"
fi

echo "══════════════════════════════════════════════"
echo "  Code Tumbler — Deploy"
echo "══════════════════════════════════════════════"

# Preflight: ensure .env and config.yaml exist
if [ ! -f .env ]; then
  echo "WARN: .env not found — copying from .env.example"
  cp .env.example .env
  echo "     Edit .env with your API keys before first run."
fi

if [ ! -f backend/config.yaml ]; then
  echo "WARN: backend/config.yaml not found — copying from example"
  cp backend/config.yaml.example backend/config.yaml
  echo "     Edit backend/config.yaml to configure your providers."
fi

# Build and start containers
echo ""
echo "Building containers..."
docker compose up --build -d $PULL_FLAG

# Wait for backend health
echo ""
echo "Waiting for backend to be healthy..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "Backend is healthy."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Backend did not become healthy within 30 seconds."
    echo "Check logs:  docker logs tumbler-backend --tail 30"
    exit 1
  fi
  sleep 1
done

echo ""
echo "══════════════════════════════════════════════"
echo "  Code Tumbler is running!"
echo "  Frontend:  http://localhost:3000"
echo "  API:       http://localhost:8000/api/health"
echo "══════════════════════════════════════════════"

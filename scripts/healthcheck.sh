#!/usr/bin/env bash
# Code Tumbler — Quick health check of all services.
# Usage:  ./scripts/healthcheck.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

pass() { printf "${GREEN}✓${NC} %s\n" "$1"; }
fail() { printf "${RED}✗${NC} %s\n" "$1"; }

echo "Code Tumbler — Health Check"
echo "───────────────────────────"

# PostgreSQL
if docker exec tumbler-postgres pg_isready -U tumbler > /dev/null 2>&1; then
  pass "PostgreSQL"
else
  fail "PostgreSQL"
fi

# Docker socket proxy
if docker inspect tumbler-docker-proxy --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
  pass "Docker socket proxy"
else
  fail "Docker socket proxy"
fi

# Backend API
if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
  pass "Backend API (port 8000)"
else
  fail "Backend API (port 8000)"
fi

# Frontend
if curl -sf http://localhost:3000 > /dev/null 2>&1; then
  pass "Frontend (port 3000)"
else
  fail "Frontend (port 3000)"
fi

# Database tables
TABLE_COUNT=$(docker exec tumbler-postgres psql -U tumbler -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null | tr -d ' ')
if [ "${TABLE_COUNT:-0}" -ge 3 ]; then
  pass "Database tables (${TABLE_COUNT} tables)"
else
  fail "Database tables (expected >= 3, got ${TABLE_COUNT:-0})"
fi

echo ""
echo "Container status:"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || docker compose ps

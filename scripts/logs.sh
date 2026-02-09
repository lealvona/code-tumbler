#!/usr/bin/env bash
# Code Tumbler — Tail logs from one or all services.
# Usage:
#   ./scripts/logs.sh              # follow all services
#   ./scripts/logs.sh backend      # follow backend only
#   ./scripts/logs.sh --since 5m   # last 5 minutes, all services

set -euo pipefail
cd "$(dirname "$0")/.."

SERVICE="${1:-}"
SINCE=""

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since)
      SINCE="--since $2"
      shift 2
      ;;
    *)
      SERVICE="$1"
      shift
      ;;
  esac
done

if [ -n "$SERVICE" ]; then
  docker compose logs -f $SINCE "$SERVICE"
else
  docker compose logs -f $SINCE
fi

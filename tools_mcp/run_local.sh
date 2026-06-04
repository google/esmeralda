#!/usr/bin/env bash
# tools_mcp/run_local.sh
set -euo pipefail

# Colors for premium CLI aesthetic
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}==================================================${NC}"
echo -e "${CYAN}🛠️  Esmeralda Local MCP Servers Runner${NC}"
echo -e "${CYAN}==================================================${NC}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo -e "${RED}❌ Error: virtual environment not found. Please run 'make bootstrap' first.${NC}"
    exit 1
fi

# Ensure log directory exists
mkdir -p "$ROOT_DIR/logs"

# Clean up function to kill background processes on exit
pids=()
cleanup() {
    echo -e "\n${YELLOW}🛑 Shutting down local MCP servers...${NC}"
    for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
        fi
    done
    echo -e "${GREEN}✅ All servers shut down clean!${NC}"
}
trap cleanup EXIT

# 1. Corporate Email on Port 8011
echo -e "${BLUE}[*] Starting Corporate Email on port 8011...${NC}"
(
    cd "$ROOT_DIR/servers/corporate-email"
    "$VENV_PYTHON" -m uvicorn main:app --port 8011 --log-level warning > "$ROOT_DIR/logs/corporate-email.log" 2>&1
) &
pids+=($!)

# 2. Income Verification on Port 8012
echo -e "${BLUE}[*] Starting Income Verification on port 8012...${NC}"
(
    cd "$ROOT_DIR/servers/income-verification-api"
    "$VENV_PYTHON" -m uvicorn main:app --port 8012 --log-level warning > "$ROOT_DIR/logs/income-verification.log" 2>&1
) &
pids+=($!)

# 3. Legacy DMS on Port 8013
echo -e "${BLUE}[*] Starting Legacy DMS on port 8013...${NC}"
(
    cd "$ROOT_DIR/servers/legacy-dms"
    "$VENV_PYTHON" -m uvicorn server:app --port 8013 --log-level warning > "$ROOT_DIR/logs/legacy-dms.log" 2>&1
) &
pids+=($!)

# Check health of each server with retries
check_health() {
    local name="$1"
    local port="$2"
    local url1="http://localhost:$port/health"
    local url2="http://localhost:$port/income-verification/health"
    local retries=10
    local count=0
    
    while [ $count -lt $retries ]; do
        if curl -s "$url1" | grep -q "ok" || curl -s "$url2" | grep -q "ok"; then
            echo -e "${GREEN}💚 $name is healthy on port $port!${NC}"
            return 0
        fi
        sleep 1
        count=$((count + 1))
    done
    
    echo -e "${RED}❌ $name failed healthcheck on port $port after $retries seconds!${NC}"
    return 1
}

all_healthy=true
for service in "corporate-email:8011" "income-verification:8012" "legacy-dms:8013"; do
    name="${service%%:*}"
    port="${service##*:}"
    if ! check_health "$name" "$port"; then
        all_healthy=false
    fi
done


if [ "$all_healthy" = true ]; then
    echo -e "\n${GREEN}🎉 All local MCP servers are up and healthy!${NC}"
    echo -e "${CYAN}--------------------------------------------------${NC}"
    echo -e "${YELLOW}To run agent tests against these local servers, run:${NC}"
    echo -e "${NC}export DMS_MCP_URL=\"http://localhost:8013/mcp\"${NC}"
    echo -e "${NC}export INCOME_VERIFICATION_URL=\"http://localhost:8012/mcp\"${NC}"
    echo -e "${NC}export EMAIL_MCP_URL=\"http://localhost:8011/mcp\"${NC}"
    echo -e "${NC}export LOCAL_MODE=\"true\"${NC}"
    echo -e "${CYAN}--------------------------------------------------${NC}"
    echo -e "${YELLOW}Press Ctrl+C to stop all servers.${NC}"
    
    # Keep script running to maintain background processes
    while true; do
        sleep 1
    done
else
    echo -e "${RED}❌ Some servers failed to start correctly. Check logs in tools_mcp/logs/${NC}"
    exit 1
fi

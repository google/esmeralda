#!/usr/bin/env bash
# Runs a command with the three local MCP servers up (corporate-email :8001,
# income-verification :8002, legacy-dms :8003).
#
# If they are already running, the command just runs. Otherwise they are started in the
# background (make run-mcp-local), the command runs once they answer /health, and they are
# torn down afterwards, also on Ctrl-C. Exits with the command's exit code.
#
# Usage: scripts/with_local_mcp.sh <command> [args...]
set -uo pipefail

PORTS=(8001 8002 8003)

all_up() {
  for port in "${PORTS[@]}"; do
    curl -s --connect-timeout 1 "http://localhost:${port}/health" >/dev/null 2>&1 || return 1
  done
}

teardown() {
  echo "🧹 Tearing down local MCP servers..."
  kill -TERM -"${make_pid}" 2>/dev/null || true
  pids=$(ss -tlnp 2>/dev/null | grep -E ":(8001|8002|8003) " | grep -o -E "pid=[0-9]+" | cut -d= -f2 | sort -u)
  [ -n "${pids}" ] && kill -TERM ${pids} 2>/dev/null || true
}

started=0
if all_up; then
  echo "ℹ️  Local MCP servers already running."
else
  echo "🚀 Starting local MCP servers (make run-mcp-local)..."
  set -m # own process group, so the whole server tree can be stopped
  make --no-print-directory run-mcp-local &
  make_pid=$!
  set +m
  started=1
  trap 'teardown; exit 130' INT TERM
  for _ in $(seq 1 30); do
    all_up && break
    sleep 1
  done
  all_up || { echo "❌ Local MCP servers did not come up."; teardown; exit 1; }
fi

"$@"
status=$?

if [ "${started}" -eq 1 ]; then
  trap - INT TERM
  teardown
fi
exit "${status}"

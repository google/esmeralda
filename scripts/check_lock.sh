#!/usr/bin/env bash
# Fails if uv.lock is out of date with the pyproject.toml files.
# Offline and read-only: it validates the committed lock (generated against public PyPI) without
# resolving or downloading anything, so it works the same on corporate and non-corporate machines.
# Used by the git pre-push hook (.githooks/pre-push) and `make lock-check` / `make test-all`.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if ! command -v uv >/dev/null 2>&1; then
  echo "⚠️  uv not found: skipping the uv.lock check (CI will still run it)." >&2
  exit 0
fi

unset UV_FROZEN # --check needs to compare the lock with pyproject.toml
if ! uv lock --check --offline --default-index https://pypi.org/simple >/dev/null 2>&1; then
  echo "❌ uv.lock is out of date with pyproject.toml." >&2
  echo "   Run 'make lock', commit uv.lock, then push again." >&2
  exit 1
fi
echo "✅ uv.lock is in sync with pyproject.toml."

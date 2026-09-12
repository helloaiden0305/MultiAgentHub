#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v uv >/dev/null 2>&1; then
  exec uv run scripts/start_all_services.py "$@"
fi

if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python scripts/start_all_services.py "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 scripts/start_all_services.py "$@"
fi

echo "未找到可用的 Python 环境。请先安装 uv，或创建 mcp-demo/.venv。" >&2
exit 1

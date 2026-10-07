#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
[[ -x "$ROOT/.venv-linux/bin/python" ]] || {
  printf '%s\n' '请先执行 bash scripts/deploy-linux.sh install' >&2
  exit 1
}
exec "$ROOT/.venv-linux/bin/python" "$ROOT/scripts/support_cli.py" "$@"

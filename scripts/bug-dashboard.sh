#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
case "${1:-status}" in
  install)
    "$ROOT/.venv-linux/bin/python" - "$ROOT" <<'PY'
from pathlib import Path
import os, sys
root = sys.argv[1]
def quote(value):
    if '\n' in value or '\r' in value:
        raise ValueError('Invalid path')
    return '"' + value.replace('\\','\\\\').replace('"','\\"').replace('%','%%') + '"'
unit = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config'))) / 'systemd/user/tulpa-bug-dashboard.service'
unit.parent.mkdir(parents=True, exist_ok=True)
unit.write_text(f'''[Unit]
Description=Tulpa local read-only bug dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory={root.replace('%', '%%')}
ExecStart={quote(root+'/.venv-linux/bin/python')} {quote(root+'/app_bug_dashboard.py')}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
''')
unit.chmod(0o600)
PY
    systemctl --user daemon-reload
    systemctl --user enable --now tulpa-bug-dashboard.service
    ;;
  status) systemctl --user status tulpa-bug-dashboard.service --no-pager ;;
  logs) journalctl --user -u tulpa-bug-dashboard.service -n 60 --no-pager ;;
  restart) systemctl --user restart tulpa-bug-dashboard.service ;;
  stop) systemctl --user stop tulpa-bug-dashboard.service ;;
  *) echo '用法：bash scripts/bug-dashboard.sh [install|status|logs|restart|stop]' >&2; exit 1 ;;
esac

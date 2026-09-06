#!/usr/bin/env bash
set -euo pipefail
quicker_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
quicker_user=${SUDO_USER:-$(id -un)}
if [[ ! -f "$quicker_root/.env" ]]; then
  echo 'Create .env from .env.example and set QUICKER_DATA_DIR to an absolute path first.' >&2
  exit 1
fi
# Python substitution avoids shell/sed interpretation of installation paths.
python3 - "$quicker_root" "$quicker_user" <<'PY'
from pathlib import Path
import sys
root, user = sys.argv[1:]
if any(c in root for c in '\n%"') or ' ' in root:
    raise SystemExit('Use an installation path without spaces, percent signs, quotes, or newlines.')
for name in ['quicker.service', 'quicker-worker.service']:
    template = (Path(root) / 'deploy' / name).read_text()
    (Path('/etc/systemd/system') / name).write_text(template.replace('QUICKER_ROOT', root).replace('QUICKER_USER', user))
PY
systemctl daemon-reload
systemctl enable --now quicker quicker-worker

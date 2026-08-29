#!/bin/bash
set -uo pipefail
cd "$(dirname "$0")" || exit 1
mkdir -p logs
LOG="logs/sync-$(date +%Y-%m-%d).log"
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -s python3 sync.py >>"$LOG" 2>&1
else
  python3 sync.py >>"$LOG" 2>&1
fi
code=$?
find logs -name 'sync-*.log' -mtime +14 -delete 2>/dev/null
for f in logs/launchd.out logs/launchd.err; do
  if [ -f "$f" ] && [ "$(wc -c <"$f")" -gt 1048576 ]; then : > "$f"; fi
done
exit $code

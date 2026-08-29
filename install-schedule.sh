#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="local.vkvideo-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}
DIR_XML="$(xml_escape "$DIR")"

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$DIR_XML/run.sh</string></array>
  <key>WorkingDirectory</key><string>$DIR_XML</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>30</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$DIR_XML/logs/launchd.out</string>
  <key>StandardErrorPath</key><string>$DIR_XML/logs/launchd.err</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
</dict>
</plist>
PL

if ! plutil -lint "$PLIST" >/dev/null 2>&1; then
  echo "generated plist is invalid, refusing to load it: $PLIST" >&2
  exit 1
fi

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
echo "loaded $LABEL (09:30 and 21:30 daily)"
echo "run now:   launchctl kickstart -k gui/$UID/$LABEL"
echo "uninstall: launchctl bootout gui/$UID/$LABEL && rm $PLIST"

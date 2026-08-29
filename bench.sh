#!/bin/bash
# usage: ./bench.sh <video-url> [seconds-per-test]
set -euo pipefail
set -m

command -v yt-dlp >/dev/null 2>&1 || { echo "yt-dlp not found" >&2; exit 1; }
URL="${1:?pass a video url}"
SECS="${2:-40}"
case "$SECS" in ''|*[!0-9]*) echo "seconds must be a positive integer" >&2; exit 1;; esac
[ "$SECS" -gt 0 ] || { echo "seconds must be a positive integer" >&2; exit 1; }

TMP="$(mktemp -d)" || exit 1
trap 'rm -rf "$TMP"' EXIT

filesize() { stat -f%z "$1" 2>/dev/null || stat -c%s "$1" 2>/dev/null || echo 0; }

total_bytes() {
  local sum=0 f
  while IFS= read -r f; do sum=$(( sum + $(filesize "$f") )); done \
    < <(find "$1" -type f 2>/dev/null)
  echo "$sum"
}

echo "=== available formats ==="
yt-dlp -F -- "$URL" 2>&1 | tail -25
echo

run() {
  local label="$1"; shift
  rm -rf "$TMP/x"; mkdir -p "$TMP/x"
  "$@" -o "$TMP/x/v.%(ext)s" -- "$URL" >/dev/null 2>&1 &
  local pid=$!
  sleep "$SECS"
  kill -TERM -- -"$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  sleep 1
  local bytes; bytes=$(total_bytes "$TMP/x")
  awk -v b="$bytes" -v s="$SECS" -v l="$label" \
    'BEGIN{printf "%-30s %8.1f MB   %6.2f MB/s   (%.1f Mbit/s)\n", l, b/1048576, b/1048576/s, b*8/1e6/s}'
}

echo "=== throughput over ${SECS}s per config ==="
run "baseline (1 connection)"  yt-dlp -f "bv*+ba/b" --concurrent-fragments 1 --downloader native
run "yt-dlp -N 16 fragments"   yt-dlp -f "bv*+ba/b" --concurrent-fragments 16 --downloader native
run "aria2c -x16 (+ -N 16)"    yt-dlp -f "bv*+ba/b" --concurrent-fragments 16 \
     --downloader http:aria2c --downloader-args "aria2c:-x16 -s16 -k1M --file-allocation=none"
echo
echo "note: the aria2c row only differs for progressive http sources. For HLS/DASH the"
echo "      fragments go through the native downloader, so rows 2 and 3 measure the same path."

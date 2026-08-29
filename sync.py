#!/usr/bin/env python3
"""Track VK Video playlists, download new uploads, prune old files."""

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
ARCHIVE = STATE / "archive.txt"
SEEN = STATE / "seen.json"
LOCK = STATE / "sync.lock"

PRUNABLE = {".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".jpg", ".jpeg", ".png",
            ".webp", ".part", ".ytdl", ".aria2", ".vtt", ".srt", ".description"}

DEFAULTS = {
    "channels": [],
    "output_dir": None,
    "max_height": 0,
    "retention_days": 0,
    "scan_depth": 30,
    "max_new_per_run": 20,
    "max_run_hours": 4,
    "first_run_take": 5,
    "per_video_timeout_min": 60,
    "concurrent_fragments": 16,
    "aria2_connections": 16,
    "write_thumbnail": False,
    "cookies_from_browser": None,
    "cookies_file": None,
    "notify": True,
}

LIST_TIMEOUT = 180


def default_output_dir():
    return "~/Movies/VKVideo" if sys.platform == "darwin" else "~/Videos/VKVideo"


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def parse_args():
    ap = argparse.ArgumentParser(
        description="Track VK Video playlists and download new uploads.")
    ap.add_argument("url", nargs="?",
                    help="playlist URL to sync instead of the configured ones")
    ap.add_argument("--name", help="folder name to use with a one-off URL")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--output", help="override output_dir; disables pruning")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be downloaded and stop")
    return ap.parse_args()


def safe_name(name):
    cleaned = re.sub(r"[^\w.-]", "-", str(name), flags=re.UNICODE).strip(".-")
    return cleaned or "channel"


def slug(url):
    return safe_name(url.rstrip("/").split("/")[-1].split("?")[0])


def num(cfg, key):
    value = cfg.get(key)
    return 0 if value is None else int(value)


def load_config(path, required):
    cfg = dict(DEFAULTS)
    p = Path(path)
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text()))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{p} is not valid JSON: {e}")
    elif required:
        raise SystemExit(
            f"no config at {p}\ncopy config.example.json to config.json and edit it, "
            f"or pass a playlist URL directly")
    cfg["output_dir"] = Path(os.path.expanduser(
        str(cfg["output_dir"] or default_output_dir())))
    return cfg


def normalise_channels(channels):
    out = []
    for i, ch in enumerate(channels):
        if not isinstance(ch, dict) or "url" not in ch:
            raise SystemExit(f"channels[{i}] needs at least a \"url\" key")
        out.append({"name": safe_name(ch.get("name") or slug(ch["url"])),
                    "url": ch["url"]})
    return out


def archived_ids():
    if not ARCHIVE.exists():
        return set()
    ids = set()
    for line in ARCHIVE.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            ids.add(parts[1])
    return ids


def mark_archived(entries):
    with ARCHIVE.open("a") as fh:
        for e in entries:
            fh.write(f"vk {e['id']}\n")


def auth_args(cfg):
    if cfg.get("cookies_from_browser"):
        return ["--cookies-from-browser", cfg["cookies_from_browser"]]
    if cfg.get("cookies_file"):
        return ["--cookies", os.path.expanduser(cfg["cookies_file"])]
    return []


def net_args(cfg):
    args = [
        "--concurrent-fragments", str(num(cfg, "concurrent_fragments") or 1),
        "--retries", "20",
        "--fragment-retries", "50",
        "--retry-sleep", "exp=2:60",
        "--socket-timeout", "30",
        "--no-abort-on-error",
    ]
    if shutil.which("aria2c"):
        x = num(cfg, "aria2_connections") or 1
        args += [
            "--downloader", "http:aria2c",
            "--downloader-args",
            f"aria2c:-x{x} -s{x} -k1M --file-allocation=none --summary-interval=15",
        ]
    return args


def fmt_selector(cfg):
    h = num(cfg, "max_height")
    if h:
        return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
    return "bv*+ba/b"


def entry_url(entry):
    return entry.get("url") or f"https://vk.com/video{entry['id']}"


def list_channel(cfg, channel):
    cmd = [
        "yt-dlp", "--flat-playlist", "--dump-json", "--ignore-errors",
        "--playlist-end", str(num(cfg, "scan_depth") or 30),
        *auth_args(cfg), "--", channel["url"],
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=LIST_TIMEOUT)
    except subprocess.TimeoutExpired:
        log(f"  ! listing timed out after {LIST_TIMEOUT}s")
        return None
    entries = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("id"):
            entries.append(item)
    if not entries and res.returncode != 0:
        tail = res.stderr.strip().splitlines()
        log(f"  ! listing failed: {tail[-1] if tail else res.returncode}")
        return None
    return entries


def run_yt_dlp(cmd, timeout):
    proc = subprocess.Popen(cmd, start_new_session=True)
    try:
        return proc.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        log(f"  ! timed out after {timeout / 60:.0f} min, killing")
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
                proc.wait(timeout=10)
                break
            except (ProcessLookupError, subprocess.TimeoutExpired):
                continue
        return False


def probe_filename(cfg, template, trim, url):
    cmd = ["yt-dlp", "--print", "filename", "--no-warnings",
           "-f", fmt_selector(cfg), "-o", template, "--trim-filenames", str(trim),
           *auth_args(cfg), "--", url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    return Path(lines[-1]) if lines else None


def download(cfg, channel, entry):
    dest = cfg["output_dir"] / channel["name"]
    dest.mkdir(parents=True, exist_ok=True)
    url = entry_url(entry)
    trim = len(str(dest)) + 1 + 120
    plain = str(dest / "%(upload_date>%Y-%m-%d|undated)s - %(title)s.%(ext)s")
    with_id = str(dest / "%(upload_date>%Y-%m-%d|undated)s - %(title)s [%(id)s].%(ext)s")

    template = plain
    planned = probe_filename(cfg, plain, trim, url)
    if planned:
        log(f"  -> {planned.name}")
        taken = any(p.is_file() and p.stem == planned.stem for p in dest.glob("*"))
        if taken:
            log("  ! that name is already taken, adding the id to keep both")
            template = with_id

    cmd = [
        "yt-dlp",
        "-f", fmt_selector(cfg),
        "--merge-output-format", "mp4",
        "-o", template,
        "--trim-filenames", str(trim),
        "--download-archive", str(ARCHIVE),
        "--no-overwrites", "--continue", "--no-mtime",
        "--newline",
        *net_args(cfg), *auth_args(cfg),
    ]
    if cfg.get("write_thumbnail"):
        cmd += ["--write-thumbnail", "--convert-thumbnails", "jpg"]
    cmd += ["--", url]

    started = time.monotonic()
    ok = run_yt_dlp(cmd, num(cfg, "per_video_timeout_min") * 60 or None)
    log(f"  {'ok' if ok else 'FAIL'} in {(time.monotonic() - started) / 60:.1f} min")
    return ok


def prune(cfg, channels):
    days = num(cfg, "retention_days")
    if not days:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for channel in channels:
        folder = cfg["output_dir"] / channel["name"]
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in PRUNABLE:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError as e:
                log(f"  ! cannot remove {path.name}: {e}")
    return removed


def notify(cfg, title, body):
    if not cfg.get("notify") or not shutil.which("osascript"):
        return
    body = body.replace("\\", "").replace('"', "'")
    subprocess.run(
        ["osascript", "-e", f'display notification "{body}" with title "{title}"'],
        capture_output=True,
    )


def main():
    args = parse_args()
    cfg = load_config(args.config, required=not args.url)
    if args.output:
        cfg["output_dir"] = Path(os.path.expanduser(args.output))
        cfg["retention_days"] = 0
    if args.url:
        cfg["channels"] = [{"name": args.name or slug(args.url), "url": args.url}]
        cfg["retention_days"] = 0
    channels = normalise_channels(cfg["channels"])
    if not channels:
        raise SystemExit("no channels configured")

    STATE.mkdir(exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("previous run still going, exiting")
        return 0

    first_run = not ARCHIVE.exists()
    known = archived_ids()
    seen, queues, list_errors = {}, [], 0

    for channel in channels:
        if "PASTE_VK" in channel["url"] or "000000000" in channel["url"]:
            log(f"skip {channel['name']}: url not configured")
            continue
        log(f"scanning {channel['name']}")
        entries = list_channel(cfg, channel)
        if entries is None:
            list_errors += 1
            continue
        log(f"  {len(entries)} items visible")
        fresh = [e for e in entries if e["id"] not in known]
        if first_run:
            take = num(cfg, "first_run_take")
            if take and len(fresh) > take:
                skipped = fresh[take:]
                fresh = fresh[:take]
                mark_archived(skipped)
                log(f"  first run: taking {take} newest, "
                    f"marking {len(skipped)} older as skipped")
        queues.append([(channel, e) for e in fresh])
        seen[channel["name"]] = {
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "visible": len(entries),
            "pending": len(fresh),
        }

    pending = []
    for i in range(max((len(q) for q in queues), default=0)):
        for q in queues:
            if i < len(q):
                pending.append(q[i])

    cap = num(cfg, "max_new_per_run")
    if cap and len(pending) > cap:
        log(f"{len(pending)} new, capping this run at {cap}")
        pending = pending[:cap]

    if args.dry_run:
        for channel, entry in pending:
            log(f"would download [{channel['name']}] {entry.get('title') or entry['id']}")
        log(f"dry run: {len(pending)} pending")
        return 1 if list_errors else 0

    budget = num(cfg, "max_run_hours") * 3600
    run_started = time.monotonic()
    done, failed = [], 0
    for channel, entry in pending:
        if budget and time.monotonic() - run_started > budget:
            log(f"time budget spent, {len(pending) - len(done) - failed} left for next run")
            break
        log(f"downloading [{channel['name']}] {entry.get('title') or entry['id']}")
        if download(cfg, channel, entry):
            done.append(entry.get("title") or entry["id"])
        else:
            failed += 1

    removed = prune(cfg, channels)
    if removed:
        log(f"pruned {removed} file(s) older than {num(cfg, 'retention_days')}d")

    SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=2))

    if done:
        notify(cfg, "VK Video", f"{len(done)} new: {done[0][:60]}")
    log(f"done: {len(done)}/{len(pending)} downloaded"
        + (f", {list_errors} listing error(s)" if list_errors else ""))
    return 1 if failed or list_errors else 0


if __name__ == "__main__":
    sys.exit(main())

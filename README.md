# vkvideo-sync

Watches VK Video playlists and downloads new uploads at the best available quality,
so you can watch them later from local storage instead of fighting a slow player.

Built around [yt-dlp](https://github.com/yt-dlp/yt-dlp). The point of the wrapper is
the bookkeeping: remembering what it already fetched, surviving interrupted downloads,
capping how much one run may do, and pruning old files.

## Why

VK's player pulls a video through a single connection. If the CDN edge is far from you,
1080p stalls while 360p limps along — the bottleneck is per-connection throughput, not
your link. Pulling the same file with 16 parallel range requests saturates the pipe and
the problem disappears. This tool automates that: it grabs new uploads in the background
and leaves you with plain `.mp4` files.

## Requirements

    brew install yt-dlp ffmpeg aria2      # macOS
    apt install yt-dlp ffmpeg aria2       # Debian/Ubuntu

`aria2` is optional but strongly recommended — it is what makes the parallel download
work for progressive MP4 sources. Without it the tool falls back to yt-dlp's native
downloader, which only parallelises fragmented (HLS/DASH) streams.

## Usage

One-off, no configuration needed:

    ./sync.py "https://vkvideo.ru/playlist/-000000000_0"

See what a run would do without downloading anything:

    ./sync.py --dry-run "https://vkvideo.ru/playlist/-000000000_0"

For recurring use, copy the example config and list your playlists in it:

    cp config.example.json config.json
    ./sync.py

`config.json` is gitignored — your own playlist URLs stay out of the repo.

    usage: sync.py [-h] [--name NAME] [--config CONFIG] [--output OUTPUT] [--dry-run] [url]

Passing `url` or `--output` disables pruning for that run, so an ad-hoc download into
some other directory can never delete anything there.

Exit code is `0` when everything asked for succeeded, `1` if any download failed, any
playlist could not be listed, or the configuration is unusable.

## How duplicates are avoided

State lives in `state/archive.txt`, one line per completed download:

    vk -000000000_456000000

Each run lists the playlist with `--flat-playlist` (cheap — ids only, nothing is
downloaded), subtracts the ids already in the archive, and fetches the remainder.
An id is recorded **only after the download finishes**, so an interrupted transfer is
retried next run rather than being treated as done.

Filenames play no part in this. Two consequences worth knowing:

- **Deleting a file does not bring it back.** Watch it, delete it, and the tool will not
  re-download it. Retention pruning works the same way.
- **`state/` is the only source of truth.** Lose it and the next run re-downloads
  everything. To re-fetch one video on purpose, delete its line from the archive.

Files are named `{upload date} - {title}.mp4`, without ids. Before each download the
target name is resolved and checked: if some other file already holds it, the video id
is appended to that one filename instead. Without this, yt-dlp's `--no-overwrites` would
report success and record the id while writing nothing, silently losing the video.

## Playlists, not channels

Use playlist URLs (`https://vkvideo.ru/playlist/-<group>_<n>`). yt-dlp's whole-channel
extractor (`vk:uservideos`) currently fails with *"Unable to extract cursor data"*, while
the playlist extractor works anonymously and lists newest-first — so scanning the top of
a playlist is enough to catch everything new.

Public playlists need no account. If you hit something restricted, point
`cookies_from_browser` at your browser (e.g. `"chrome"`) or `cookies_file` at an exported
cookie jar.

## Configuration

| key | meaning |
|---|---|
| `channels` | list of `{name, url}`; `name` becomes the subfolder |
| `output_dir` | parent directory; each channel gets its own subfolder under it |
| `max_height` | `0` for best available, or e.g. `1080` |
| `retention_days` | delete downloads older than this; `0` disables |
| `scan_depth` | how many newest playlist items to inspect per run |
| `max_new_per_run` | cap on downloads per run; leftovers roll into the next one |
| `max_run_hours` | stop starting new downloads after this long; `0` disables |
| `first_run_take` | on a fresh archive, take the N newest and mark the rest as skipped |
| `per_video_timeout_min` | kill a single download after this long; `0` disables |
| `concurrent_fragments` | parallel fragments for HLS/DASH sources |
| `aria2_connections` | parallel connections for progressive MP4 sources |
| `write_thumbnail` | save cover images next to the videos |
| `notify` | macOS notification when a run downloads something |

`retention_days` only ever touches media files sitting directly in a configured
channel's own subfolder. It does not recurse, does not follow symlinks, does not remove
directories, and ignores file types it did not produce — so pointing `output_dir` at a
shared folder cannot destroy unrelated files.

`first_run_take` writes the older ids straight into the archive as skipped, so they are
never fetched later. If you want them after all, delete their lines from
`state/archive.txt`.

## Files

| path | what |
|---|---|
| `sync.py` | the whole job — portable, no macOS dependencies |
| `run.sh` | wrapper: writes a dated log, asks macOS not to idle-sleep |
| `install-schedule.sh` | optional launchd agent, twice daily |
| `bench.sh` | compares downloader configurations on one video |
| `state/archive.txt` | the dedup source — back this up |
| `logs/` | one log per day, kept 14 days |

## Scheduling

macOS:

    ./install-schedule.sh                              # 09:30 and 21:30 daily
    launchctl bootout gui/$UID/local.vkvideo-sync      # remove

A `flock` makes a second run exit instead of racing an unfinished one, and
`per_video_timeout_min` stops a wedged download from holding that lock forever.

`run.sh` wraps the run in `caffeinate -i -s`, which prevents *idle* sleep, and prevents
system sleep only while on AC power. On battery, or with the lid closed, the machine can
still sleep mid-download — the transfer resumes on the next run rather than being lost.

On Linux, skip the three shell scripts and point a systemd timer or cron entry at
`python3 sync.py`. `sync.py` itself is portable; only its desktop notification is
macOS-specific, and that degrades to a no-op elsewhere.

## Note

Downloads content you already have access to, for personal offline viewing. Respect the
rights of whoever made the videos.

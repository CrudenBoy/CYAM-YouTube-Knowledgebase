"""Download video and subtitles via yt-dlp.

Surfaces the best local file paths for processing (prefers local path if
source is a file, otherwise downloads to a temp folder).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def is_url(path: str) -> bool:
    return path.startswith(("http://", "https://", "www."))


def download(source: str, out_dir: Path) -> dict:
    """Resolve source to a local video file + subtitle file."""
    if not is_url(source):
        p = Path(source).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"Local file not found: {source}")
        return {"video_path": str(p), "subtitle_path": None, "info": {}}

    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed. Install with: brew install yt-dlp")

    out_dir.mkdir(parents=True, exist_ok=True)
    # Output template: %(id)s.%(ext)s to keep it simple and safe.
    template = str(out_dir / "%(id)s.%(ext)s")

    # 1. Get info
    cmd_info = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "--print-json",
        "--skip-download",
        source,
    ]
    result = subprocess.run(cmd_info, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"yt-dlp failed to fetch info: {result.stderr.strip()}")
    info = json.loads(result.stdout)

    # 2. Download video + auto-subs
    # We prefer small-ish files (720p or less) to keep processing fast.
    cmd_dl = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "--write-auto-subs",
        "--write-subs",
        "--sub-format", "vtt",
        "--no-playlist",
        "-o", template,
        source,
    ]
    subprocess.run(cmd_dl, capture_output=True)

    # 3. Locate files
    video_path = None
    subtitle_path = None

    # yt-dlp might have changed the extension during merge (e.g. mkv, mp4)
    # so we look for the file with the matching ID.
    for p in out_dir.iterdir():
        if p.stem == info["id"]:
            if p.suffix == ".vtt":
                subtitle_path = str(p)
            elif p.suffix in (".mp4", ".mkv", ".webm", ".mov", ".avi"):
                video_path = str(p)

    if not video_path:
        # Fallback: look for any video file in the folder if the ID match failed
        videos = [
            p for p in out_dir.iterdir()
            if p.suffix in (".mp4", ".mkv", ".webm", ".mov", ".avi")
        ]
        if videos:
            video_path = str(videos[0])

    if not video_path:
        raise SystemExit(f"Failed to download video from {source}")

    return {
        "video_path": video_path,
        "subtitle_path": subtitle_path,
        "info": {
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "id": info.get("id"),
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: download.py <url-or-path> [<out-dir>]", file=sys.stderr)
        raise SystemExit(2)

    src = sys.argv[1]
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("download")
    res = download(src, out)
    print(json.dumps(res, indent=2))

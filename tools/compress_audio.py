"""
compress_audio.py: convert long .wav sounds to high-quality .mp3 and update the project to match.

Needs ffmpeg. On Windows:  winget install Gyan.FFmpeg   (then open a new terminal)

Usage (from the repo root):
    python tools/compress_audio.py --dry-run         (show what would be converted and the savings)
    python tools/compress_audio.py                   (convert)
    python tools/compress_audio.py --min-seconds 3   (only convert sounds 3 s or longer)

What it does, for every .wav sound at least --min-seconds long (default 1):
  - encodes it as VBR MP3 (LAME -q:a 2, ~190 kbps, sounds the same as the .wav at game volumes)
  - replaces the .wav file(s) in assets/sounds with the .mp3
  - updates every sprite that uses it in project/ (assetId, md5ext, dataFormat, rate, sampleCount)
Short sounds stay .wav: they're tiny, and .wav starts with zero delay.
Sounds that are already .mp3 are left alone.
Afterwards run pack.py to build the smaller .sb3.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

QUALITY = "2"   # LAME VBR quality: 0 = best/biggest, 2 = transparent, 4 = smaller


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def probe(path: Path):
    """(sample rate, duration in seconds) of an audio file."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    return int(info["streams"][0]["sample_rate"]), float(info["format"]["duration"])


def main():
    parser = argparse.ArgumentParser(description="Convert long .wav sounds to .mp3.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="repo root (default: the folder above tools/)")
    parser.add_argument("--min-seconds", type=float, default=1.0,
                        help="only convert sounds at least this long (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("ffmpeg not found. On Windows run:  winget install Gyan.FFmpeg   then open a new terminal.")

    repo = args.repo.resolve()
    json_files = [repo / "project" / "stage.json", *(repo / "project" / "sprites").rglob("*.json")]
    targets = {f: json.loads(f.read_text(encoding="utf-8")) for f in json_files if f.is_file()}

    # every .wav file in assets/sounds, grouped by contents (shared sounds have several copies)
    files_by_hash = {}
    for p in (repo / "assets" / "sounds").rglob("*.wav"):
        files_by_hash.setdefault(md5(p.read_bytes()), []).append(p)

    # which .wav assets are long enough to convert
    candidates = {}                       # assetId -> (name, seconds)
    for t in targets.values():
        for s in t.get("sounds", []):
            if s["dataFormat"] != "wav" or s["assetId"] in candidates:
                continue
            seconds = s["sampleCount"] / s["rate"] if s.get("rate") else 0
            if seconds >= args.min_seconds:
                candidates[s["assetId"]] = (s["name"], seconds)

    missing = [a for a in candidates if a not in files_by_hash]
    if missing:
        print(f"Warning: {len(missing)} sound(s) not found in assets/sounds; skipping them. Run unpack.py first?")
    todo = [a for a in candidates if a in files_by_hash]
    before = sum(files_by_hash[a][0].stat().st_size for a in todo)
    print(f"{len(todo)} .wav sound(s) of {args.min_seconds:g}s or longer, {before / 1e6:.1f} MB")

    if args.dry_run:
        for a in sorted(todo, key=lambda a: -files_by_hash[a][0].stat().st_size)[:15]:
            p = files_by_hash[a][0]
            print(f"  {p.stat().st_size / 1e6:6.1f} MB  {p.relative_to(repo).as_posix()}")
        print(f"\nExpected after: roughly {before / 8 / 1e6:.0f} MB.  Dry run only. Nothing was changed.")
        return

    # encode each unique sound once
    new_info = {}                         # old assetId -> dict of new fields
    after = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, a in enumerate(todo, 1):
            src = files_by_hash[a][0]
            out = Path(tmp) / f"{a}.mp3"
            print(f"  [{i}/{len(todo)}] {src.relative_to(repo).as_posix()}", flush=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                            "-c:a", "libmp3lame", "-q:a", QUALITY, "-map_metadata", "-1", str(out)],
                           check=True)
            data = out.read_bytes()
            rate, seconds = probe(out)
            new_id = md5(data)
            new_info[a] = {"assetId": new_id, "md5ext": f"{new_id}.mp3", "dataFormat": "mp3",
                           "rate": rate, "sampleCount": round(seconds * rate)}
            after += len(data)
            # replace every copy of the .wav with the .mp3
            for wav in files_by_hash[a]:
                mp3 = wav.with_suffix(".mp3")
                n = 2
                while mp3.exists():
                    mp3 = wav.with_name(f"{wav.stem}_{n}.mp3")
                    n += 1
                mp3.write_bytes(data)
                wav.unlink()

    # update every sprite that uses a converted sound
    changed = 0
    for f, t in targets.items():
        touched = False
        for s in t.get("sounds", []):
            if s["assetId"] in new_info:
                s.update(new_info[s["assetId"]])
                touched = True
        if touched:
            f.write_text(json.dumps(t, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            changed += 1

    print(f"\nConverted {len(new_info)} sound(s): {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")
    print(f"Updated {changed} sprite file(s). Now run:  python tools/pack.py")


if __name__ == "__main__":
    main()

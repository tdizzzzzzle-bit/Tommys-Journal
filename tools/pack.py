"""
pack.py: rebuild a playable .sb3 from the repo (the reverse of unpack.py).

Usage (from the repo root):
    python tools/pack.py                              -> build/Tommy's Journal.sb3
    python tools/pack.py --out "D:/somewhere/game.sb3"

It reads project/meta.json, project/stage.json and project/sprites/**,
puts every "@video:<file>" reference back as an embedded video from assets/videos/,
and finds each costume, sound and font in assets/ by its contents (so file names don't matter).
If anything the project uses can't be found, it stops and lists it instead of building a broken game.
"""

import argparse
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

PLACEHOLDER = "@video:"
VIDEO_MIME = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".ogv": "video/ogg"}
ASSET_DIRS = ("costumes", "sounds", "fonts")


def main():
    parser = argparse.ArgumentParser(description="Build an .sb3 from the repo.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="repo root (default: the folder above tools/)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output .sb3 (default: build/Tommy's Journal.sb3)")
    args = parser.parse_args()

    repo = args.repo.resolve()
    out = (args.out or repo / "build" / "Tommy's Journal.sb3").resolve()
    problems = []

    # ------------------------------------------------ 1. put project.json back together
    meta_path = repo / "project" / "meta.json"
    if not meta_path.is_file():
        parser.error(f"{meta_path} not found. Run unpack.py first.")
    project = json.loads(meta_path.read_text(encoding="utf-8"))
    target_files = project.pop("targets")
    targets = []
    for rel in target_files:
        path = repo / "project" / rel
        if not path.is_file():
            problems.append(f"sprite file missing: project/{rel}")
            continue
        targets.append(json.loads(path.read_text(encoding="utf-8")))
    project = {"targets": targets, **project}

    # ------------------------------------------------ 2. embed videos
    videos_dir = repo / "assets" / "videos"
    video_cache = {}
    video_count = 0

    def embed(value):
        nonlocal video_count
        if not (isinstance(value, str) and value.startswith(PLACEHOLDER)):
            return value
        name = value[len(PLACEHOLDER):]
        if name not in video_cache:
            file = videos_dir / name
            if not file.is_file():
                problems.append(f"video missing: assets/videos/{name}")
                video_cache[name] = value
            else:
                mime = VIDEO_MIME.get(file.suffix.lower(), "video/mp4")
                video_cache[name] = f"data:{mime};base64," + base64.b64encode(file.read_bytes()).decode()
        video_count += 1
        return video_cache[name]

    for t in targets:
        for var in t.get("variables", {}).values():
            var[1] = embed(var[1])
        for lst in t.get("lists", {}).values():
            lst[1] = [embed(item) for item in lst[1]]

    # ------------------------------------------------ 3. find every asset by its contents
    pool = {}                                  # md5 -> path
    for d in ASSET_DIRS:
        for p in (repo / "assets" / d).rglob("*"):
            if p.is_file() and p.name != ".gitkeep":
                pool.setdefault(hashlib.md5(p.read_bytes()).hexdigest(), p)

    needed = {}                                # md5ext -> md5
    for t in targets:
        for kind in ("costumes", "sounds"):
            for a in t.get(kind, []):
                needed[a["md5ext"]] = a["assetId"]
                if a["assetId"] not in pool:
                    problems.append(f"{kind[:-1]} missing: '{a['name']}' in {t['name']} ({a['md5ext']})")
    for font in project.get("customFonts", []) or []:
        if font.get("system") is False and "md5ext" in font:
            h = font["md5ext"].rpartition(".")[0]
            needed[font["md5ext"]] = h
            if h not in pool:
                problems.append(f"font missing: '{font['family']}' ({font['md5ext']})")

    if problems:
        print(f"Can't build: {len(problems)} problem(s).")
        for p in problems[:30]:
            print(f"  {p}")
        if len(problems) > 30:
            print(f"  ... and {len(problems) - 30} more")
        return 1

    # ------------------------------------------------ 4. write the .sb3
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")     # write to a temp file so a failed build never leaves half a game
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("project.json", json.dumps(project, ensure_ascii=False, separators=(",", ":")))
        for md5ext, h in sorted(needed.items()):
            z.write(pool[h], md5ext)
    tmp.replace(out)

    size = out.stat().st_size / 1e6
    print(f"Built {out}")
    print(f"  {len(targets)} targets, {len(needed)} asset files, {video_count} embedded video(s), {size:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

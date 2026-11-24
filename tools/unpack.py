"""
unpack.py: turn your game into readable, git-friendly files.

Usage (from the repo root):
    python tools/unpack.py "path/to/Tommy's Journal.sb3" --dry-run   (show what would change)
    python tools/unpack.py "path/to/Tommy's Journal.sb3"             (do it)
    python tools/unpack.py project/project.json                      (also works with a bare project.json)

What it writes:
    project/meta.json                    extensions, fonts, monitors, settings, sprite order
    project/stage.json                   the Stage
    project/sprites/<Folder>/<Name>.json one file per sprite ("mother//2" -> sprites/mother/2.json)
    assets/costumes/<Folder>/<Name>/...  costumes, named as in the editor
    assets/sounds/<Folder>/<Name>/...    sounds, named as in the editor
    assets/fonts/<Family>.ttf            custom fonts
    assets/videos/...                    videos found inside lists/variables (data:video/... text)

Videos: every list item or variable holding a video becomes a short reference like
"@video:10_lego.mp4". pack.py turns it back into the embedded video.
If the .sb3 holds a different video in a slot that already has a file, the repo's file is KEPT
(so your re-encoded videos aren't replaced). Add --replace-videos to take the .sb3's version.

The assets/costumes, assets/sounds and assets/fonts folders are rebuilt to match the game
every run: treat them as generated. Files there that no sprite uses are moved to
assets/_unmatched/ (never deleted), so you can check them.
"""

import argparse
import base64
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ILLEGAL = r'[<>:"/\\|?*\x00-\x1f]'          # characters Windows won't allow in file names
GENERATED_ASSET_DIRS = ("costumes", "sounds", "fonts")
VIDEO_EXT = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "video/ogg": ".ogv"}
PLACEHOLDER = "@video:"


# ---------------------------------------------------------------- helpers

def clean(name: str) -> str:
    """Make one path part safe for Windows."""
    name = re.sub(ILLEGAL, "_", str(name)).strip().rstrip(".")
    return name or "_"


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class UniqueNames:
    """Hands out paths, adding _2, _3... on clashes. Case-insensitive, because Windows is."""
    def __init__(self):
        self.taken = set()

    def get(self, path: Path) -> Path:
        candidate, n = path, 2
        while str(candidate).lower() in self.taken:
            candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
            n += 1
        self.taken.add(str(candidate).lower())
        return candidate


def to_json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


# ---------------------------------------------------------------- loading

def load_input(path: Path):
    """Return (project dict, {md5ext: bytes-loader}) from an .sb3 or a project.json."""
    if path.suffix.lower() in (".sb3", ".zip"):
        z = zipfile.ZipFile(path)
        project = json.loads(z.read("project.json").decode("utf-8"))
        files = {name: (lambda n=name: z.read(n)) for name in z.namelist() if name != "project.json"}
        return project, files
    return json.loads(path.read_text(encoding="utf-8")), {}


def old_video_slots(repo: Path):
    """Read the previous unpack's JSON files: which video file each list/variable slot pointed at."""
    slots = {}
    files = [repo / "project" / "stage.json", *(repo / "project" / "sprites").rglob("*.json")]
    for f in files:
        if not f.is_file():
            continue
        try:
            target = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        for key, slot_value in iter_slots(target):
            if isinstance(slot_value, str) and slot_value.startswith(PLACEHOLDER):
                slots[key] = slot_value[len(PLACEHOLDER):]
    return slots


def iter_slots(target):
    """Yield ((target name, kind, id, index), value) for every variable and list item."""
    name = target["name"]
    for vid, var in target.get("variables", {}).items():
        yield (name, "var", vid, 0), var[1]
    for lid, lst in target.get("lists", {}).items():
        for i, item in enumerate(lst[1]):
            yield (name, "list", lid, i), item


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Unpack an .sb3 / project.json into the repo layout.")
    parser.add_argument("input", type=Path, help=".sb3 file or project.json")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="repo root (default: the folder above tools/)")
    parser.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    parser.add_argument("--replace-videos", action="store_true",
                        help="if a video in the .sb3 differs from the repo's file, overwrite the repo's file")
    args = parser.parse_args()

    repo = args.repo.resolve()
    src = args.input.resolve()
    if not src.is_file():
        parser.error(f"input not found: {src}")
    project, zip_files = load_input(src)
    dry = args.dry_run
    report = {"videos": [], "warnings": []}

    # ------------------------------------------------ 1. folder name for every target
    names = UniqueNames()
    target_paths = []                         # (target, json path relative to project/, asset folder)
    for t in project["targets"]:
        if t["isStage"]:
            folder = Path("Stage")
            json_rel = Path("stage.json")
        else:
            folder = names.get(Path(*[clean(p) for p in t["name"].split("//")]))
            json_rel = Path("sprites") / f"{folder.as_posix()}.json"
        target_paths.append((t, json_rel, folder))

    # ------------------------------------------------ 2. videos -> files + placeholders
    videos_dir = repo / "assets" / "videos"
    existing_videos = {md5(p.read_bytes()): p.name for p in videos_dir.glob("*") if p.is_file()} \
        if videos_dir.is_dir() else {}
    previous = old_video_slots(repo)
    video_names = UniqueNames()
    for p in videos_dir.glob("*") if videos_dir.is_dir() else []:
        video_names.taken.add(str(videos_dir / p.name).lower())
    videos_to_write = {}                      # file name -> bytes

    def handle_video(key, text, label, index):
        header, _, b64 = text.partition(",")
        mime = header[5:].split(";")[0]
        data = base64.b64decode(b64)
        h = md5(data)
        if h in existing_videos:                               # same video already in the repo
            return PLACEHOLDER + existing_videos[h]
        if key in previous:                                    # slot already has a file, but contents differ
            name = previous[key]
            if args.replace_videos:
                videos_to_write[name] = data
                report["videos"].append(f"replaced  {name}  ({label} item {index + 1})")
            else:
                report["videos"].append(f"kept repo version of {name}  ({label} item {index + 1} differs "
                                        f"in the .sb3; use --replace-videos to take it)")
            return PLACEHOLDER + name
        base = clean(re.sub(r"\s*//dontsave//\s*", "", label))  # brand-new video
        name = video_names.get(videos_dir / f"{base}_{index + 1:02d}{VIDEO_EXT.get(mime, '.bin')}").name
        videos_to_write[name] = data
        existing_videos[h] = name
        report["videos"].append(f"new       {name}  ({label} item {index + 1})")
        return PLACEHOLDER + name

    for t, _, _ in target_paths:
        for vid, var in t.get("variables", {}).items():
            if isinstance(var[1], str) and var[1].startswith("data:video/"):
                var[1] = handle_video((t["name"], "var", vid, 0), var[1], var[0], 0)
                report["warnings"].append(f"variable '{var[0]}' in {t['name']} holds a video")
        for lid, lst in t.get("lists", {}).items():
            for i, item in enumerate(lst[1]):
                if isinstance(item, str) and item.startswith("data:video/"):
                    lst[1][i] = handle_video((t["name"], "list", lid, i), item, lst[0], i)

    # ------------------------------------------------ 3. where every asset should live
    layout = {}                               # absolute path -> assetId (md5)
    asset_names = UniqueNames()
    for t, _, folder in target_paths:
        for kind in ("costumes", "sounds"):
            for a in t.get(kind, []):
                dest = asset_names.get(repo / "assets" / kind / folder / f"{clean(a['name'])}.{a['dataFormat']}")
                layout[dest] = a["assetId"]
    for font in project.get("customFonts", []) or []:
        if font.get("system") is False and "md5ext" in font:
            stem, _, ext = font["md5ext"].rpartition(".")
            dest = asset_names.get(repo / "assets" / "fonts" / f"{clean(font['family'])}.{ext}")
            layout[dest] = stem

    # ------------------------------------------------ 4. pool of asset bytes: .sb3 first, then repo files
    pool = {}                                 # md5 -> callable returning bytes
    for name, loader in zip_files.items():
        pool.setdefault(name.rpartition(".")[0], loader)
    current = {}                              # existing generated file -> md5
    for d in GENERATED_ASSET_DIRS:
        for p in (repo / "assets" / d).rglob("*"):
            if p.is_file() and p.name != ".gitkeep":
                h = md5(p.read_bytes())
                current[p] = h
                pool.setdefault(h, lambda p=p: p.read_bytes())

    needed = set(layout.values())
    missing = sorted(needed - pool.keys())
    unmatched = [p for p, h in current.items() if h not in needed]
    to_write = {p: h for p, h in layout.items() if current.get(p) != h}
    to_remove = [p for p in current if p not in layout]

    # ------------------------------------------------ 5. JSON files
    json_files = {}                           # absolute path -> text
    meta = {k: v for k, v in project.items() if k != "targets"}
    meta["targets"] = [p.as_posix() for _, p, _ in target_paths]  # sprite order, for pack.py
    json_files[repo / "project" / "meta.json"] = to_json(meta)
    for t, json_rel, _ in target_paths:
        json_files[repo / "project" / json_rel] = to_json(t)
    old_jsons = [p for p in (repo / "project" / "sprites").rglob("*.json")] \
        if (repo / "project" / "sprites").is_dir() else []
    stale_jsons = [p for p in old_jsons if p not in json_files]
    changed_jsons = [p for p, text in json_files.items()
                     if not p.is_file() or p.read_text(encoding="utf-8") != text]

    # ------------------------------------------------ report
    print(f"Unpacking {src.name}: {len(target_paths)} targets, {len(layout)} asset files\n")
    print(f"  JSON files changed/new:   {len(changed_jsons)}")
    print(f"  JSON files removed:       {len(stale_jsons)}")
    print(f"  asset files new/renamed:  {sum(1 for h in to_write.values() if h not in missing)}")
    print(f"  asset files removed:      {len(to_remove) - len(unmatched)} (renamed/moved)")
    if unmatched:
        print(f"  unused assets -> assets/_unmatched/: {len(unmatched)}")
    for line in report["videos"]:
        print(f"  video: {line}")
    for line in report["warnings"]:
        print(f"  WARNING: {line}")
    if missing:
        print(f"\n  MISSING {len(missing)} asset(s): the project uses them but they're in neither the input nor the repo:")
        for h in missing[:20]:
            users = [rel(p, repo) for p, x in layout.items() if x == h]
            print(f"    {h}  (would be {users[0]})")
        if len(missing) > 20:
            print(f"    ... and {len(missing) - 20} more")

    if dry:
        if changed_jsons:
            print("\n  first JSON changes:")
            for p in changed_jsons[:10]:
                print(f"    {rel(p, repo)}")
        print("\nDry run only. Nothing was changed.")
        return

    # ------------------------------------------------ 6. apply
    # 6a. read every asset we still need into a temp folder, so moving/renaming can't lose anything
    tmp = repo / "assets" / ".unpack_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    for h in needed - set(missing):
        (tmp / h).write_bytes(pool[h]())
    # 6b. park unused files, clear generated folders, lay files back out
    for p in unmatched:
        dest = repo / "assets" / "_unmatched" / p.name
        while dest.exists():
            dest = dest.with_name(f"{dest.stem}_x{dest.suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), dest)
    for d in GENERATED_ASSET_DIRS:
        if (repo / "assets" / d).is_dir():
            shutil.rmtree(repo / "assets" / d)
    for dest, h in layout.items():
        if h in missing:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp / h, dest)
    shutil.rmtree(tmp)

    # 6c. videos
    videos_dir.mkdir(parents=True, exist_ok=True)
    for name, data in videos_to_write.items():
        (videos_dir / name).write_bytes(data)

    # 6d. JSON
    for p in stale_jsons:
        p.unlink()
    for p, text in json_files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    sprites_dir = repo / "project" / "sprites"
    for d in sorted(sprites_dir.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()

    print("\nDone.")
    if (repo / "project" / "project.json").exists():
        print("Note: project/project.json is no longer needed (it's split into the files above). "
              "Delete it before committing.")


if __name__ == "__main__":
    sys.exit(main())

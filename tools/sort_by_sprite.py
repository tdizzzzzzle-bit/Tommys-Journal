"""
sort_by_sprite.py: move costumes and sounds into folders named after the sprite that uses them,
and rename them from their hash names (a3f9...c2.png) to their names in the editor (walk1.png).

Run from the repo root after sort_files.py:
    python sort_by_sprite.py --dry-run     (only shows what would happen)
    python sort_by_sprite.py               (moves the files)

Result:
    assets/costumes/mother/2/idle.png
    assets/sounds/Player/Player/footstep.wav
    assets/costumes/Stage/backdrop1.svg
    assets/costumes/_unused/...           (files no sprite uses)

Sprite names like "mother//2" become folders mother/2.
A file used by several sprites is copied into each of their folders.
Git stores identical files only once, so the copies cost no extra space in the repo.
"""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ASSET_DIRS = {"costumes": "assets/costumes", "sounds": "assets/sounds"}
ILLEGAL = r'[<>:"/\\|?*\x00-\x1f]'   # characters Windows won't allow in file names


def clean(name: str) -> str:
    """Make a name safe for Windows file names."""
    name = re.sub(ILLEGAL, "_", name).strip().rstrip(".")
    return name or "_"


def sprite_folder(sprite_name: str) -> Path:
    """'TOMMY BOSSFIGHT//"Him"' -> TOMMY BOSSFIGHT/_Him_"""
    return Path(*[clean(part) for part in sprite_name.split("//")])


def md5_of(file: Path) -> str:
    return hashlib.md5(file.read_bytes()).hexdigest()


def unique(dest: Path, taken: set) -> Path:
    """Avoid two different assets ending up with the same file name."""
    candidate, n = dest, 2
    while candidate in taken:
        candidate = dest.with_name(f"{dest.stem}_{n}{dest.suffix}")
        n += 1
    taken.add(candidate)
    return candidate


def main():
    parser = argparse.ArgumentParser(description="Sort assets into per-sprite folders.")
    parser.add_argument("--repo", type=Path, default=Path("."), help="repo root (default: current folder)")
    parser.add_argument("--project", type=Path, default=None,
                        help="path to project.json (default: <repo>/project/project.json)")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    args = parser.parse_args()

    repo = args.repo.resolve()
    project_path = args.project or repo / "project" / "project.json"
    if not project_path.is_file():
        parser.error(f"project.json not found: {project_path}")
    project = json.loads(project_path.read_text(encoding="utf-8"))

    # 1. From project.json: which sprite uses which asset, and what it's called there.
    #    assetId (md5 of the file's contents) -> list of (sprite folder, asset name, extension)
    uses = {"costumes": {}, "sounds": {}}
    for target in project["targets"]:
        folder = sprite_folder(target["name"])
        for kind in ("costumes", "sounds"):
            for asset in target.get(kind, []):
                uses[kind].setdefault(asset["assetId"], []).append(
                    (folder, asset["name"], asset["dataFormat"]))

    taken = set()
    moved = copied = unused = 0

    for kind, rel in ASSET_DIRS.items():
        root = repo / rel
        if not root.is_dir():
            continue
        # only loose files sitting directly in assets/costumes or assets/sounds
        for file in sorted(p for p in root.iterdir() if p.is_file() and p.name != ".gitkeep"):
            # match by file name first (hash-named files from an .sb3), then by contents
            asset_id = file.stem if file.stem in uses[kind] else md5_of(file)
            targets = uses[kind].get(asset_id)

            if not targets:
                dests = [unique(root / "_unused" / file.name, taken)]
                unused += 1
            else:
                dests = [unique(root / folder / f"{clean(name)}.{fmt}", taken)
                         for folder, name, fmt in targets]

            for i, dest in enumerate(dests):
                last = i == len(dests) - 1
                if args.dry_run:
                    print(f"[dry run] {file.relative_to(repo)}  ->  {dest.relative_to(repo)}")
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                if last:
                    shutil.move(file, dest)   # the last use takes the original
                    moved += 1
                else:
                    shutil.copy2(file, dest)  # earlier uses get copies
                    copied += 1

    print("\n=== Summary ===")
    if args.dry_run:
        print("Dry run only. Nothing was changed.")
    else:
        print(f"  moved:  {moved}")
        print(f"  copied: {copied} (assets shared by more than one sprite)")
    print(f"  unused: {unused} (in _unused/ folders; no sprite references them)")


if __name__ == "__main__":
    main()

"""
sort_files.py: copy loose game files into the Tommy's Journal repo layout.

Usage:
    python sort_files.py <source folder> <repo folder>             (copies files)
    python sort_files.py <source folder> <repo folder> --dry-run   (only shows what would happen)
    python sort_files.py <source folder> <repo folder> --move      (moves instead of copying)

Copies by default, so your originals are never touched.
Searches every subfolder of the source.
"""

import argparse
import filecmp
import shutil
from pathlib import Path

# extension -> destination folder (relative to the repo root)
DESTINATIONS = {
    # sounds
    ".wav": "assets/sounds", ".mp3": "assets/sounds", ".ogg": "assets/sounds",
    ".flac": "assets/sounds", ".m4a": "assets/sounds", ".aac": "assets/sounds",
    # costumes / images
    ".png": "assets/costumes", ".svg": "assets/costumes", ".jpg": "assets/costumes",
    ".jpeg": "assets/costumes", ".gif": "assets/costumes", ".bmp": "assets/costumes",
    ".webp": "assets/costumes",
    # videos
    ".mp4": "assets/videos", ".webm": "assets/videos", ".mov": "assets/videos",
    # custom extensions
    ".js": "extensions",
    # notes
    ".md": "docs", ".txt": "docs",
    # fonts
    ".ttf": "assets/fonts", ".otf": "assets/fonts",
}

# files that should NOT be copied into the repo
SKIP = {".sb3", ".sprite3", ".zip"}

UNSORTED = "unsorted"  # anything with an unknown extension lands here for you to check


def unique_path(dest: Path) -> Path:
    """If dest already exists, add _2, _3, ... before the extension."""
    n = 2
    candidate = dest
    while candidate.exists():
        candidate = dest.with_name(f"{dest.stem}_{n}{dest.suffix}")
        n += 1
    return candidate


def main():
    parser = argparse.ArgumentParser(description="Sort game files into the repo layout.")
    parser.add_argument("source", type=Path, help="folder with your game files")
    parser.add_argument("repo", type=Path, help="root folder of the repo (tommys-journal)")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    parser.add_argument("--move", action="store_true", help="move files instead of copying")
    args = parser.parse_args()

    source = args.source.resolve()
    repo = args.repo.resolve()
    counts = {}
    skipped, duplicates, renamed = [], [], []

    for file in sorted(source.rglob("*")):
        if not file.is_file() or file.name == ".gitkeep":
            continue
        if repo in file.parents:          # never re-sort files already inside the repo
            continue

        ext = file.suffix.lower()
        if ext in SKIP:
            skipped.append(file)
            continue

        # project.json is special: it goes in project/
        folder = "project" if file.name == "project.json" else DESTINATIONS.get(ext, UNSORTED)
        dest = repo / folder / file.name

        # same name already there: skip if identical, otherwise rename
        if dest.exists():
            if filecmp.cmp(file, dest, shallow=False):
                duplicates.append(file)
                continue
            dest = unique_path(dest)
            renamed.append((file, dest))

        if args.dry_run:
            print(f"[dry run] {file.relative_to(source)}  ->  {dest.relative_to(repo)}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            (shutil.move if args.move else shutil.copy2)(file, dest)

        counts[folder] = counts.get(folder, 0) + 1

    # summary
    print("\n=== Summary ===")
    for folder, n in sorted(counts.items()):
        print(f"  {folder:20} {n} file(s)")
    if duplicates:
        print(f"  identical copies skipped: {len(duplicates)}")
    if renamed:
        print("  renamed because of a name clash:")
        for src, dst in renamed:
            print(f"    {src.relative_to(source)}  ->  {dst.name}")
    if skipped:
        print("  not copied (project/archive files):")
        for f in skipped:
            print(f"    {f.relative_to(source)}")
    if counts.get(UNSORTED):
        print(f"\n  Check '{UNSORTED}/': those extensions weren't recognised.")
    if args.dry_run:
        print("\nDry run only. Nothing was changed.")


if __name__ == "__main__":
    main()

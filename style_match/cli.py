"""Command-line interface for photo-style-match."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from .apply import apply_profile
from .lut import export_cube_lut
from .profile import LookProfile, build_profile

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def _collect_images(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            out.extend(sorted(f for f in path.iterdir() if f.suffix.lower() in IMAGE_EXTS))
        elif path.is_file():
            out.append(path)
        else:
            print(f"warning: {p} not found, skipping", file=sys.stderr)
    return out


def cmd_build_profile(args: argparse.Namespace) -> None:
    refs = _collect_images(args.refs)
    if not refs:
        sys.exit("No reference images found.")
    profile = build_profile(refs)
    profile.save(Path(args.out))
    print(f"Built profile from {len(refs)} image(s) -> {args.out}")


def cmd_apply(args: argparse.Namespace) -> None:
    profile = LookProfile.load(Path(args.profile))
    image = Image.open(args.input)
    graded = apply_profile(image, profile, strength=args.strength)
    graded.save(args.output)
    print(f"Graded {args.input} -> {args.output} (strength={args.strength})")


def cmd_export_lut(args: argparse.Namespace) -> None:
    profile = LookProfile.load(Path(args.profile))
    export_cube_lut(profile, Path(args.out), size=args.size)
    print(f"Exported {args.size}^3 .cube LUT -> {args.out}")


def cmd_process(args: argparse.Namespace) -> None:
    profile_dir = Path(args.profile_dir)
    profile = LookProfile.load(profile_dir / "profile.json")

    inbox = profile_dir / "inbox"
    graded_dir = profile_dir / "graded"
    graded_dir.mkdir(exist_ok=True)

    images = _collect_images([str(inbox)])
    if not images:
        sys.exit(f"No images found in {inbox}. Drop photos there and re-run.")

    for img_path in images:
        image = Image.open(img_path)
        graded = apply_profile(image, profile, strength=args.strength)
        out_path = graded_dir / f"{img_path.stem}_graded{img_path.suffix}"
        graded.save(out_path)
        print(f"Graded {img_path.name} -> {out_path}")

    print(f"Done: {len(images)} photo(s) -> {graded_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="style-match", description="Match photos to a reference photographer's look."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build-profile", help="Build a look profile from reference images")
    p_build.add_argument("refs", nargs="+", help="Reference image files or folders")
    p_build.add_argument("--out", required=True, help="Output profile .json path")
    p_build.set_defaults(func=cmd_build_profile)

    p_apply = sub.add_parser("apply", help="Apply a look profile to a photo")
    p_apply.add_argument("--profile", required=True, help="Path to profile .json")
    p_apply.add_argument("--input", required=True, help="Input image path")
    p_apply.add_argument("--output", required=True, help="Output image path")
    p_apply.add_argument("--strength", type=float, default=0.85, help="Blend strength 0-1 (default 0.85)")
    p_apply.set_defaults(func=cmd_apply)

    p_lut = sub.add_parser("export-lut", help="Bake a profile into a .cube LUT")
    p_lut.add_argument("--profile", required=True, help="Path to profile .json")
    p_lut.add_argument("--out", required=True, help="Output .cube path")
    p_lut.add_argument("--size", type=int, default=33, help="LUT lattice size (default 33)")
    p_lut.set_defaults(func=cmd_export_lut)

    p_process = sub.add_parser(
        "process", help="Grade every photo in <profile-dir>/inbox using <profile-dir>/profile.json"
    )
    p_process.add_argument("profile_dir", help="Path to a photographer's profile folder, e.g. profiles/larry-chen")
    p_process.add_argument("--strength", type=float, default=0.85, help="Blend strength 0-1 (default 0.85)")
    p_process.set_defaults(func=cmd_process)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

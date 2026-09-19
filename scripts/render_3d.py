#!/usr/bin/env python3
"""Render 3D views of the board for the README.

Writes, under the output directory (``docs/`` by default):

    render-iso.png        perspective view from the front left, the hero image
    render-top.png        straight down
    render-bottom.png     straight up
    render-turntable.gif  one full turn of the board, one frame per 10 degrees

The stills and turntable frames come from kicad-cli's ray tracer (KiCad 9 or
newer). The frames are stitched with ffmpeg, whose two-pass palette keeps the
colours clean at a sensible file size; pass --no-gif to skip that step.

Usage:
    scripts/render_3d.py [-o OUTPUT_DIR] [--frames N] [--no-gif]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOARD = REPO / "deepsea-light-pcb.kicad_pcb"

# Camera elevation for the angled views. -40 puts the camera above the board
# looking down; the Z term is the turntable angle.
TILT = -40

# Each still: output name and its kicad-cli arguments. The top and bottom views
# use the basic renderer because the high-quality one draws a floor with
# shadows, and from below you see the top-side components' shadows through it.
STILLS = {
    "render-iso.png": [
        "--quality", "high", "--perspective",
        "--rotate", f"{TILT},0,30", "--zoom", "0.9",
    ],
    "render-top.png": ["--quality", "basic", "--side", "top", "--zoom", "1.6"],
    "render-bottom.png": ["--quality", "basic", "--side", "bottom", "--zoom", "1.6"],
}
STILL_SIZE = ("1600", "1000")

# Turntable frames are smaller: the GIF is the biggest file in the repo and
# every board change re-commits it. 4:3 suits the board as it turns end-on.
FRAME_SIZE = ("720", "540")
FRAME_RATE = "12"


def find_kicad_cli() -> str:
    """Locate kicad-cli on PATH, falling back to the macOS application bundle."""
    found = shutil.which("kicad-cli")
    if found:
        return found
    bundled = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    if bundled.exists():
        return str(bundled)
    sys.exit("kicad-cli not found. Install KiCad 9 or newer, or put it on PATH.")


def render(cli: str, out: Path, size: tuple[str, str], *args: str) -> None:
    cmd = [
        cli, "pcb", "render",
        "--background", "opaque",
        "-w", size[0], "-h", size[1],
        *args,
        "-o", str(out), str(BOARD),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"kicad-cli failed: {' '.join(cmd)}\n{result.stdout}{result.stderr}")


def make_gif(frames_dir: Path, out: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg not found; install it or pass --no-gif.")
    # One shared palette for the whole loop, then Bayer dithering, which
    # compresses far better than the default error diffusion on a slow pan.
    filters = (
        "split[a][b];"
        "[a]palettegen=stats_mode=full[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-framerate", FRAME_RATE, "-i", str(frames_dir / "frame_%03d.png"),
        "-vf", filters, "-loop", "0", str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"ffmpeg failed: {' '.join(cmd)}\n{result.stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-o", "--output", type=Path, default=REPO / "docs")
    parser.add_argument("--frames", type=int, default=36, help="turntable frames per revolution")
    parser.add_argument("--no-gif", action="store_true", help="skip the turntable GIF")
    args = parser.parse_args()

    cli = find_kicad_cli()
    args.output.mkdir(parents=True, exist_ok=True)

    for name, render_args in STILLS.items():
        print(f"Rendering {name}")
        render(cli, args.output / name, STILL_SIZE, *render_args)

    if args.no_gif:
        return

    with tempfile.TemporaryDirectory() as tmp:
        frames_dir = Path(tmp)
        for i in range(args.frames):
            angle = 360 * i / args.frames
            print(f"Rendering turntable frame {i + 1}/{args.frames} ({angle:.0f} deg)")
            render(
                cli, frames_dir / f"frame_{i:03d}.png", FRAME_SIZE,
                "--quality", "high", "--perspective",
                "--rotate", f"{TILT},0,{angle:g}", "--zoom", "0.85",
            )
        print("Encoding render-turntable.gif")
        make_gif(frames_dir, args.output / "render-turntable.gif")


if __name__ == "__main__":
    main()

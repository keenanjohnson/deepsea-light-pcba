#!/usr/bin/env python3
"""Render the board and schematic images for the README.

Writes, under the output directory (``docs/`` by default):

    render-iso.png        perspective view from the front left, the hero image
    render-top.png        straight down
    render-bottom.png     straight up
    render-turntable.gif  one full turn of the board, one frame per 10 degrees
    render-schematic.png  the schematic, trimmed to its content

The stills and turntable frames come from kicad-cli's ray tracer (KiCad 9 or
newer). The frames are stitched with ffmpeg, whose two-pass palette keeps the
colours clean at a sensible file size; pass --no-gif to skip that step.

The schematic is exported as SVG, rasterised with rsvg-convert, and cropped to
its drawn extent (plus a margin) so the empty A4 page borders do not dominate.

Usage:
    scripts/render.py [-o OUTPUT_DIR] [--frames N] [--no-gif]
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
SCHEMATIC = REPO / "deepsea-light-pcb.kicad_sch"

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

# Schematic raster width before cropping, and the white margin kept around the
# drawn content. Pixels lighter than WHITE_THRESHOLD count as background.
SCHEMATIC_WIDTH = "2400"
SCHEMATIC_MARGIN = 40
WHITE_THRESHOLD = 250


def find_kicad_cli() -> str:
    """Locate kicad-cli on PATH, falling back to the macOS application bundle."""
    found = shutil.which("kicad-cli")
    if found:
        return found
    bundled = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    if bundled.exists():
        return str(bundled)
    sys.exit("kicad-cli not found. Install KiCad 9 or newer, or put it on PATH.")


def require(tool: str, hint: str) -> str:
    found = shutil.which(tool)
    if not found:
        sys.exit(f"{tool} not found; {hint}.")
    return found


def run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        sys.exit(f"{what} failed: {' '.join(cmd)}\n{result.stderr.decode(errors='replace')}")
    return result


def render(cli: str, out: Path, size: tuple[str, str], *args: str) -> None:
    cmd = [
        cli, "pcb", "render",
        "--background", "opaque",
        "-w", size[0], "-h", size[1],
        *args,
        "-o", str(out), str(BOARD),
    ]
    run(cmd, "kicad-cli")


def make_gif(frames_dir: Path, out: Path) -> None:
    ffmpeg = require("ffmpeg", "install it or pass --no-gif")
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
    run(cmd, "ffmpeg")


def content_box(png: Path) -> tuple[int, int, int, int]:
    """Bounding box (x, y, w, h) of the non-white pixels in a PNG.

    Reads the image back through ffmpeg as raw RGB so no imaging library is
    needed; the per-row scan runs in C via bytes.strip, which is fast enough
    for a few million pixels.
    """
    ffmpeg = require("ffmpeg", "install it")
    ffprobe = require("ffprobe", "it ships with ffmpeg")
    dims = run([ffprobe, "-v", "error", "-show_entries", "stream=width,height",
                "-of", "csv=p=0", str(png)], "ffprobe").stdout.decode().strip()
    width, height = (int(v) for v in dims.split(","))
    raw = run([ffmpeg, "-loglevel", "error", "-i", str(png),
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], "ffmpeg").stdout
    # Snap near-white bytes to 0xff so anti-aliased fringes count as background.
    near_white = bytes(0xFF if v >= WHITE_THRESHOLD else v for v in range(256))
    stride = width * 3
    left, right, top, bottom = width, 0, height, 0
    for y in range(height):
        row = raw[y * stride:(y + 1) * stride].translate(near_white)
        stripped = row.lstrip(b"\xff")
        if not stripped:
            continue
        x0 = (len(row) - len(stripped)) // 3
        x1 = len(row.rstrip(b"\xff")) // 3 + 1
        left, right = min(left, x0), max(right, x1)
        top, bottom = min(top, y), max(bottom, y + 1)
    if right <= left:
        sys.exit(f"{png} is blank")
    return left, top, right - left, bottom - top


def render_schematic(cli: str, out: Path) -> None:
    rsvg = require("rsvg-convert", "install librsvg")
    ffmpeg = require("ffmpeg", "install it")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # -e drops the drawing sheet (the title block is empty), -n leaves the
        # background unset so rsvg-convert can paint it white.
        run([cli, "sch", "export", "svg", "-e", "-n", "-o", str(tmp_dir), str(SCHEMATIC)],
            "kicad-cli")
        svgs = list(tmp_dir.glob("*.svg"))
        if len(svgs) != 1:
            sys.exit(f"expected one schematic sheet, got {len(svgs)}")
        full = tmp_dir / "schematic.png"
        run([rsvg, "-w", SCHEMATIC_WIDTH, "-b", "white", str(svgs[0]), "-o", str(full)],
            "rsvg-convert")
        x, y, w, h = content_box(full)
        m = SCHEMATIC_MARGIN
        # ffmpeg's crop filter clamps x/y so the box stays inside the image.
        run([ffmpeg, "-y", "-loglevel", "error", "-i", str(full),
             "-vf", f"crop=min(iw\\,{w + 2 * m}):min(ih\\,{h + 2 * m}):{x - m}:{y - m}",
             str(out)], "ffmpeg")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-o", "--output", type=Path, default=REPO / "docs")
    parser.add_argument("--frames", type=int, default=36, help="turntable frames per revolution")
    parser.add_argument("--no-gif", action="store_true", help="skip the turntable GIF")
    args = parser.parse_args()

    cli = find_kicad_cli()
    args.output.mkdir(parents=True, exist_ok=True)

    print("Rendering render-schematic.png")
    render_schematic(cli, args.output / "render-schematic.png")

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

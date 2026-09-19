#!/usr/bin/env python3
"""Generate JLCPCB fabrication files for the board in this repository.

Produces, under the output directory (``fab/`` by default):

    <board>-gerbers.zip   gerbers + Excellon drill files, ready to upload
    gerbers/              the same files loose, for inspection
    <board>-bom.csv       bill of materials, with LCSC part numbers
    <board>-cpl.csv       component positions, for assembly
    <board>-schematic.pdf schematic, for the build record
    drc.json / erc.json   the reports the checks below were gated on

The BOM and position files come from Fabrication-Toolkit, which knows JLCPCB's
part orientation conventions and corrects the rotations accordingly; KiCad's
raw position export does not. The toolkit is fetched at a pinned release into
.fabrication-toolkit/ on first use and needs a Python that can import pcbnew,
which KiCad's own Python provides.

Design rule checks run first and abort the export on any error, on an unrouted
connection, or on a board that disagrees with the schematic. Those are the
things that produce a bad board.

Electrical rule checks run too and are reported in full, but do not stop the
export: this schematic's remaining ERC errors are annotation issues such as
missing power flags, which do not reach the gerbers. Pass --strict to block on
them as well.

DRC warnings never block. The footprint libraries carry silkscreen text below
JLCPCB's legend minimums, which is cosmetic.

Usage:
    scripts/generate_fab.py [-o OUTPUT_DIR] [--strict] [--skip-checks]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Non-copper layers every JLCPCB order needs. Copper layers are read from the
# board itself so this keeps working if the stackup changes.
TECHNICAL_LAYERS = [
    "F.Paste",
    "B.Paste",
    "F.SilkS",
    "B.SilkS",
    "F.Mask",
    "B.Mask",
    "Edge.Cuts",
]

# Fabrication-Toolkit, for the BOM and position files. It reads the part number
# from each footprint's LCSC field, which this project's symbols carry.
TOOLKIT_REPO = "https://github.com/bennymeg/Fabrication-Toolkit"
TOOLKIT_TAG = "5.3.1"
TOOLKIT_DIR = REPO / ".fabrication-toolkit"


def find_kicad_cli() -> str:
    """Locate kicad-cli on PATH, falling back to the macOS application bundle."""
    found = shutil.which("kicad-cli")
    if found:
        return found
    bundled = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    if bundled.exists():
        return str(bundled)
    sys.exit("kicad-cli not found. Install KiCad 9 or newer, or put it on PATH.")


def run(cli: str, *args: str) -> None:
    result = subprocess.run([cli, *args], capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        sys.exit(f"kicad-cli {' '.join(args[:3])} failed with {result.returncode}")


def copper_layers(board: Path) -> list[str]:
    """Return the board's copper layers, front to back, in stackup order."""
    text = board.read_text()
    block = re.search(r"\(layers\n(.*?)\n\t\)", text, re.S)
    if not block:
        sys.exit(f"could not read the layer table from {board.name}")
    layers = re.findall(r'\(\d+ "([^"]+)" (?:signal|power|mixed|jumper)', block.group(1))
    if not layers:
        sys.exit(f"no copper layers found in {board.name}")
    return layers


def report_counts(path: Path, kind: str) -> int:
    """Print a summary of a DRC or ERC report and return the blocking count."""
    data = json.loads(path.read_text())

    if kind == "drc":
        violations = data.get("violations", [])
        extra = {
            "unrouted connections": len(data.get("unconnected_items", [])),
            "schematic mismatches": len(data.get("schematic_parity", [])),
        }
    else:
        violations = [v for sheet in data.get("sheets", []) for v in sheet.get("violations", [])]
        extra = {}

    errors = [v for v in violations if v.get("severity") == "error"]
    warnings = [v for v in violations if v.get("severity") == "warning"]

    blocking = len(errors) + sum(extra.values())
    parts = [f"{len(errors)} errors", f"{len(warnings)} warnings"]
    parts += [f"{count} {label}" for label, count in extra.items()]
    print(f"  {kind.upper()}: {', '.join(parts)}")

    for violation in errors:
        print(f"    error: {violation.get('type')}: {violation.get('description')}")
    for label, count in extra.items():
        if count:
            print(f"    error: {count} {label}(s)")

    by_type: dict[str, int] = {}
    for violation in warnings:
        by_type[violation.get("type", "?")] = by_type.get(violation.get("type", "?"), 0) + 1
    for name, count in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"    warning: {count} x {name}")

    return blocking


def clear_output_dir(out: Path) -> None:
    """Empty the output directory, refusing to touch anything we did not write.

    The export starts from a clean directory so a stale gerber can never end up
    in the upload. Since the directory is caller-supplied, delete it only when
    every entry looks like our own output.
    """
    if not out.exists():
        return
    if not out.is_dir():
        sys.exit(f"{out} exists and is not a directory")

    ours = {"gerbers", "drc.json", "erc.json"}
    strays = [
        p.name
        for p in out.iterdir()
        if p.name not in ours and p.suffix not in {".zip", ".csv", ".pdf"}
    ]
    if strays:
        sys.exit(
            f"{out} holds files this script did not create ({', '.join(sorted(strays)[:5])}). "
            "Refusing to delete it. Point --output somewhere else."
        )
    shutil.rmtree(out)


def find_kicad_python() -> str:
    """Locate a Python that can import pcbnew: the system one, or KiCad's own on macOS."""
    candidates = [
        shutil.which("python3"),
        "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3",
    ]
    for python in candidates:
        if python and Path(python).exists():
            probe = subprocess.run([python, "-c", "import pcbnew, wx"], capture_output=True)
            if probe.returncode == 0:
                return python
    sys.exit("no Python with the pcbnew module found. Install KiCad 9 or newer.")


def ensure_toolkit() -> Path:
    """Fetch Fabrication-Toolkit at the pinned release unless it is already present."""
    if not (TOOLKIT_DIR / "plugins" / "cli.py").exists():
        print(f"Fetching Fabrication-Toolkit {TOOLKIT_TAG}...")
        result = subprocess.run(
            ["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1", "--branch", TOOLKIT_TAG, TOOLKIT_REPO, str(TOOLKIT_DIR)]
        )
        if result.returncode != 0:
            sys.exit("could not fetch Fabrication-Toolkit")
    return TOOLKIT_DIR


def export_assembly_files(board: Path, bom: Path, cpl: Path) -> None:
    """Run Fabrication-Toolkit and move its BOM and position files into place.

    The toolkit writes into production/ next to the board and reports failures
    in its log rather than its exit code, so success is judged by the files it
    leaves behind. Its own gerber archive and netlist are discarded: the
    gerbers come from kicad-cli above, where every plot option is explicit.
    """
    toolkit = ensure_toolkit()
    production = board.parent / "production"
    shutil.rmtree(production, ignore_errors=True)

    result = subprocess.run(
        [find_kicad_python(), "-m", "plugins.cli",
         "--path", str(board),
         "--autoTranslate",  # apply JLCPCB's rotation and position corrections
         "--excludeDNP",
         "--nonInteractive"],
        cwd=toolkit, capture_output=True, text=True)

    bom_src = production / "bom.csv"
    cpl_src = production / "positions.csv"
    if result.returncode != 0 or not (bom_src.exists() and cpl_src.exists()):
        sys.stderr.write(result.stdout + result.stderr)
        sys.exit("Fabrication-Toolkit did not produce the BOM and position files")

    shutil.move(bom_src, bom)
    shutil.move(cpl_src, cpl)
    shutil.rmtree(production)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", default=REPO / "fab", type=Path, help="output directory (default: fab/)")
    parser.add_argument("--strict", action="store_true", help="also block the export on ERC errors")
    parser.add_argument("--skip-checks", action="store_true", help="export even if the checks fail")
    args = parser.parse_args()

    board = next(REPO.glob("*.kicad_pcb"), None)
    schematic = next(REPO.glob("*.kicad_sch"), None)
    if board is None or schematic is None:
        sys.exit("no .kicad_pcb / .kicad_sch found at the top of the repository")
    name = board.stem

    cli = find_kicad_cli()
    out = args.output
    gerber_dir = out / "gerbers"
    clear_output_dir(out)
    gerber_dir.mkdir(parents=True)

    print(f"Board: {board.name}")

    # -- Checks -------------------------------------------------------------
    print("Checking...")
    run(cli, "sch", "erc", "--format", "json", "--severity-all", "-o", str(out / "erc.json"), str(schematic))
    run(cli, "pcb", "drc", "--format", "json", "--severity-all", "--schematic-parity",
        "--refill-zones", "-o", str(out / "drc.json"), str(board))
    erc_errors = report_counts(out / "erc.json", "erc")
    drc_errors = report_counts(out / "drc.json", "drc")

    blocking = drc_errors + (erc_errors if args.strict else 0)
    if erc_errors and not args.strict:
        print(f"  note: {erc_errors} ERC errors do not block the export. Use --strict to change that.")
    if blocking and not args.skip_checks:
        sys.exit(f"\n{blocking} blocking problem(s). Fix them, or re-run with --skip-checks.")

    # -- Gerbers and drill --------------------------------------------------
    layers = copper_layers(board) + TECHNICAL_LAYERS
    print(f"Plotting {len(layers)} layers: {', '.join(layers)}")
    run(cli, "pcb", "export", "gerbers",
        "--output", str(gerber_dir) + "/",
        "--layers", ",".join(layers),
        # JLCPCB's parser is happiest with plain RS-274X and no embedded netlist.
        "--no-x2", "--no-netlist",
        "--precision", "6",
        # Refill zones if the saved fill is stale, so the pours are never missing.
        "--check-zones",
        str(board))

    # Plated and non-plated holes go in separate files. This board has non-plated
    # mounting holes, and merging them risks getting them plated.
    run(cli, "pcb", "export", "drill",
        "--output", str(gerber_dir) + "/",
        "--format", "excellon",
        "--drill-origin", "absolute",
        "--excellon-units", "mm",
        "--excellon-zeros-format", "decimal",
        "--excellon-separate-th",
        str(board))

    archive = out / f"{name}-gerbers.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(gerber_dir.iterdir()):
            if path.is_file():
                zf.write(path, path.name)  # flat, as JLCPCB expects
    print(f"Wrote {archive.relative_to(REPO)} ({len(zipfile.ZipFile(archive).namelist())} files)")

    # -- Assembly and documentation -----------------------------------------
    bom = out / f"{name}-bom.csv"
    cpl = out / f"{name}-cpl.csv"
    export_assembly_files(board, bom, cpl)
    print(f"Wrote {bom.relative_to(REPO)} and {cpl.relative_to(REPO)}")

    run(cli, "sch", "export", "pdf", "--output", str(out / f"{name}-schematic.pdf"), str(schematic))

    print(f"\nFab files in {out.relative_to(REPO)}/ — upload {archive.name} to JLCPCB.")
    print("Order settings to match this design: 4 layers, 1.6 mm, 1 oz outer copper.")


if __name__ == "__main__":
    main()

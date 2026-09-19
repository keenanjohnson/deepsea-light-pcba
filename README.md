# deepsea-light-pcba
A PCB for controlling Deepsea Lights

## Connectors

![Connector overview](docs/connectors.png)

Rendered automatically by [kicad-callouts](https://github.com/keenanjohnson/kicad-callouts) via GitHub Actions whenever the board changes.


## Fabrication

Generate everything needed to order the board from JLCPCB:

```
scripts/generate_fab.py
```

This writes to `fab/` (gitignored): a flat `*-gerbers.zip` to upload, the same
gerbers and Excellon drill files loose for inspection, a BOM with LCSC part
numbers, a position file for assembly, a schematic PDF, and the DRC/ERC
reports.

The BOM and position file are produced by
[Fabrication-Toolkit](https://github.com/bennymeg/Fabrication-Toolkit), which
applies JLCPCB's part rotation conventions. The script fetches it at a pinned
release into `.fabrication-toolkit/` (gitignored) on first run. It needs a
Python that can import `pcbnew`; on macOS the script finds KiCad's bundled one.
If a part still arrives rotated, add an `FT Rotation Offset` field (degrees,
counter-clockwise) to that symbol and regenerate.

The Pico (U1) and the SparkFun RS-485 breakout (U7) are off-the-shelf modules
that plug into female 2.54 mm headers, so they are excluded from the BOM and
position file. The headers themselves are J1–J4: pad-less footprints
(`Socket_1x*_P2.54mm_NoPads`) placed over the module pin rows, carrying the
LCSC numbers so JLCPCB solders the sockets and the modules are fitted by hand.

Design rule and unrouted-connection errors abort the export. ERC errors are
reported but do not, since the remaining ones are annotation issues such as
missing power flags; pass `--strict` to block on those too. Silkscreen warnings
are expected: several footprint libraries use legend text below JLCPCB's 1.0 mm
minimum height and 0.15 mm minimum stroke.

Order settings that match the design rules in `deepsea-light-pcb.kicad_pro`:
**4 layers, 1.6 mm thick, 1 oz outer copper.** The [Fab files
workflow](.github/workflows/fab.yml) builds the same package in CI and attaches
it to release tags.

## Stackup

| Layer  | Net         | Purpose |
|--------|-------------|---------|
| F.Cu   | `GND`       | Logic ground, 0.1 mm above the signal layer so the PWM input and UART reference a quiet plane |
| In1.Cu | `GND`       | Logic ground, carries most of the signal routing |
| In2.Cu | `+24V`      | Light supply |
| B.Cu   | `LIGHT_GND` | Light return, tightly coupled to the 24 V plane above it |

`GND` and `LIGHT_GND` are deliberately separate on the board. The Pico and the
RS-485 transceiver run from the Navigator's 5 V supply, with 24 V reaching only
the light connectors, following the bench testing in
[CCR_development#34](https://github.com/Seattle-Aquarium/CCR_development/issues/34#issuecomment-4383756606)
that traced light flicker to shared power. There are no ground stitching vias:
JLCPCB does not offer blind or buried vias, so stitching would have to punch
both power planes and put `GND` within clearance of `LIGHT_GND` at every drop.
The two ground pours meet at the through-hole ground pins instead.

The `Power` netclass gives `+24V` and `LIGHT_GND` a 0.5 mm clearance, wider than
the 0.09 mm JLCPCB allows, because those nets land on hand-soldered terminal
blocks where a bridge to the ground pour would put 24 V on the Pico.

### Software Repo

This board is designed to run micropython from this repo: https://github.com/Seattle-Aquarium/CCR_ROV_survey_methods/tree/main/lighting/code

## Random Notes

I usually use this project to get parts / footprints:

https://github.com/TousstNicolas/JLC2KiCad_lib

### Example usage

JLC2KiCadLib C3818565        -dir Deepsea_light_lib            \
                             -model_dir Deepsea_light_dir      \
                             -footprint_lib Deepsea_light_lib  \
                             -symbol_lib_dir Deepsea_light_dir \
                             -symbol_lib Deepsea_light_lib

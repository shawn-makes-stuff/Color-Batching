"""Printer types ColorStack refuses: a reference file edited to two nozzles, to a belt printer, to sequential printing, a file ColorStack wrote.
python tools/test_guards.py"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from color_batching.parse import PROCESSED, PROCESSED_BEFORE, parse  # noqa: E402

SRC = ROOT / "samples" / "cube1h43.gcode"
# a real two-nozzle printer: two nozzle_diameter values and single-extruder multi-material off
EDITS = {"two nozzles": ("; nozzle_diameter = 0.4", "; nozzle_diameter = 0.4,0.4\n; single_extruder_multi_material = 0"),
         "belt printer": ("; nozzle_diameter = 0.4", "; nozzle_diameter = 0.4\n; belt_printer = 1"),
         "sequential printing": ("; print_sequence = by layer", "; print_sequence = by object"),
         "already processed": ("; print_sequence = by layer", "; print_sequence = by layer\n; " + PROCESSED),
         "processed by 0.1.0": ("; print_sequence = by layer", "; print_sequence = by layer\n; " + PROCESSED_BEFORE[0])}

if __name__ == "__main__":
    text = SRC.read_text(encoding="utf-8")
    parse(str(SRC))                         # the file itself is accepted
    # one nozzle per filament listed but single-extruder multi-material (PrusaSlicer writes that): accepted
    one = os.path.join(tempfile.mkdtemp(), "one.gcode")
    Path(one).write_text(text.replace("; nozzle_diameter = 0.4", "; nozzle_diameter = 0.4,0.4,0.4,0.4", 1), encoding="utf-8")
    parse(one)
    print("4 nozzle_diameter values with single_extruder_multi_material = 1: accepted")
    for name, (old, new) in EDITS.items():
        assert old in text, old
        path = os.path.join(tempfile.mkdtemp(), "guard.gcode")
        Path(path).write_text(text.replace(old, new, 1), encoding="utf-8")
        try:
            parse(path)
        except ValueError as e:
            print(f"{name}: refused ({e})")
            continue
        raise SystemExit(f"{name}: NOT refused")

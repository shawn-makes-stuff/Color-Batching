"""Nothing is handed between materials that would not bond like the original: Stitch with T1 relabelled PETG must keep
every PETG line PETG and every PLA line PLA.  python tools/test_materials.py"""
import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colorstack.config import DEFAULTS, apply  # noqa: E402
from colorstack.pipeline import run  # noqa: E402
from colorstack.reader import load, read  # noqa: E402

ALL_PLA = "; filament_type = PLA;PLA;PLA;PLA"


def tools_at(path):
    """(z, x, y) of each segment middle -> tool, prime tower left out."""
    out, tool, typ = {}, 0, ""
    for i, kind, v in read(load(path)):
        if kind == "tool":
            tool = v[0]
        elif kind == "type":
            typ = v
        elif kind == "move" and v.de > 0 and v.xy > 1e-9 and typ != "Prime tower":
            out.setdefault((round(v.z1, 2), round((v.x0 + v.x1) / 2, 1), round((v.y0 + v.y1) / 2, 1)), tool)
    return out


if __name__ == "__main__":
    text = (ROOT / "samples" / "stitch.gcode").read_text(encoding="utf-8")
    assert ALL_PLA in text
    d = Path(tempfile.mkdtemp())
    (d / "mixed.gcode").write_text(text.replace(ALL_PLA, "; filament_type = PLA;PETG;PLA;PLA"), encoding="utf-8")
    r = run(str(d / "mixed.gcode"), str(d / "out.gcode"), apply(copy.deepcopy(DEFAULTS), {"nozzle": {"contact": 0.2}}))
    a, b = tools_at(str(d / "mixed.gcode")), tools_at(str(d / "out.gcode"))
    crossed = sum(1 for k, t in b.items() if k in a and (a[k] == 1) != (t == 1))
    print(f"swaps {r['processed']['swaps']}, segments moved between PETG and PLA: {crossed}")
    if crossed:
        raise SystemExit("plastic was handed between materials")

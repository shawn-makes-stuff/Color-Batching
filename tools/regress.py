"""Regression run: process the reference files with fixed settings (not colorstack.toml) and record the results.

python tools/regress.py LABEL [file ... | --variants]  writes work/regress/LABEL/*.gcode and LABEL.json
python tools/regress.py --diff LABEL_A LABEL_B compares two runs (swaps, times, output hashes)
"""
import copy
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colorstack.config import DEFAULTS, apply  # noqa: E402
from colorstack.pipeline import run  # noqa: E402

FILES = [
    "work/cube_34m_print_1h2m_flush.gcode",                    # Anycubic, Slope Lift, M204
    "samples/cube1h43.gcode",                                   # Orca 2.4.2
    "samples/stitch.gcode",                                     # 4 colours, arcs
    "work/ECC_0.4_Support_test_Elegoo PLA _0.2_30m42s.gcode",   # grid supports, own support layer height
    "work/ECC_0.4_hat_Elegoo PLA _0.28_2h22m.gcode",            # Orca 2.5
    "samples/compat/vegeta.gcode",                              # PA on, tree supports, textured, 899 layers
]
# the user's 3MF (spinner + TerrorCube) with features turned on: samples/compat/make_variants.py
VARIANTS = [f"samples/compat/v/{n}.gcode" for n in ("small", "filaments", "adaptive_pa", "travel", "scarf_arcs",
                                                     "fw_retract", "surface")]
SETTINGS = {"nozzle": {"contact": 0.2}}
OUT = ROOT / "work" / "regress"


def process(label, files):
    cfg = apply(copy.deepcopy(DEFAULTS), SETTINGS)
    (OUT / label).mkdir(parents=True, exist_ok=True)
    results = {}
    for f in files:
        src = ROOT / f
        dst = OUT / label / (src.stem + ".gcode")
        t0 = time.time()
        r = run(str(src), str(dst), cfg)
        took = time.time() - t0
        o, p = r["original"], r["processed"]
        results[f] = {"swaps": [o["swaps"], p["swaps"]], "total_s": [round(o["total_s"]), round(p["total_s"])],
                      "sha": hashlib.sha256(dst.read_bytes()).hexdigest()[:16], "seconds": round(took, 1)}
        print(f"{src.stem[:40]:40} swaps {o['swaps']:>5} -> {p['swaps']:<5} time {o['total_s'] / 60:7.1f} -> "
              f"{p['total_s'] / 60:7.1f} min  {took:6.1f}s  {results[f]['sha']}", flush=True)
    (OUT / f"{label}.json").write_text(json.dumps(results, indent=1))


def diff(a, b):
    ra, rb = (json.loads((OUT / f"{x}.json").read_text()) for x in (a, b))
    for f in ra.keys() | rb.keys():
        x, y = ra.get(f), rb.get(f)
        if not x or not y:
            print(f"{Path(f).stem[:40]:40} only in {a if x else b}")
            continue
        same = "identical" if x["sha"] == y["sha"] else "DIFFERENT"
        print(f"{Path(f).stem[:40]:40} {same:9} swaps {x['swaps'][1]} -> {y['swaps'][1]}  "
              f"time {x['total_s'][1] / 60:.1f} -> {y['total_s'][1] / 60:.1f} min  "
              f"{x['seconds']}s -> {y['seconds']}s")


if __name__ == "__main__":
    if sys.argv[1] == "--diff":
        diff(sys.argv[2], sys.argv[3])
    else:
        args = sys.argv[2:]
        process(sys.argv[1], VARIANTS if args == ["--variants"] else args or FILES)

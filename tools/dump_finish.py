"""Finish a writer output as the pipeline does (estimate both files, anchor to the slicer's total, M73 and header
rewrite) and dump the figures with floats as 64-bit patterns (the format of cpp/tools/finish.cpp).
python tools/dump_finish.py original.gcode written.gcode final.gcode figures.txt"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colorstack.config import DEFAULTS  # noqa: E402
from colorstack.estimate import anchor, estimate, file_estimate, machine_limits  # noqa: E402
from colorstack.parse import _parse_settings  # noqa: E402
from colorstack.reader import load  # noqa: E402
from colorstack.summary import stamp_header, stamp_progress  # noqa: E402


def b(v):
    return "None" if v is None else struct.pack(">d", float(v)).hex()


def figures(r):
    ext = " ".join(f"{t}={b(v)}" for t, v in sorted(r["extruded"].items(), key=lambda kv: (kv[0] is not None, kv[0] or 0)))
    into = " ".join(f"{t}={n}" for t, n in sorted(r["swaps_into"].items()))
    return (f"motion {b(r['motion_s'])} dwell {b(r['dwell_s'])} swaps {r['swaps']} swap {b(r['swap_s'])} "
            f"total {b(r['total_s'])} each {b(r['swap_each_s'])} source {r['source']}\n  extruded {ext}\n  into {into}\n")


if __name__ == "__main__":
    original, written = load(sys.argv[1]), load(sys.argv[2])
    lim = machine_limits(_parse_settings(original), DEFAULTS["printer"])
    o = estimate(original, lim)
    slicer = file_estimate(original)
    steps = []
    p = estimate(written, lim, steps)
    anchor(o, p, slicer, lim)
    lines = stamp_progress(written, steps, p["swap_each_s"], p["total_s"])
    stamp_header(lines, o, p)
    Path(sys.argv[3]).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    with open(sys.argv[4], "w", encoding="utf-8", newline="\n") as f:
        f.write(f"slicer {b(slicer)}\nsteps {len(steps)}\noriginal " + figures(o) + "processed " + figures(p))

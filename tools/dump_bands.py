"""Plan a file (default settings, one variant: plan + tower compensation, as the pipeline does), write the bands with
every path in full (floats as 64-bit patterns, tab-separated), and the Python writer's G-code for those bands, so the
C++ writer can be compared on exactly the same plan (cpp/tools/write_bands).
python tools/dump_bands.py in.gcode bands.txt expected.gcode [per_object] [staggered]"""
import copy
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from color_batching.config import DEFAULTS, apply  # noqa: E402
from color_batching.geometry import Nozzle  # noqa: E402
from color_batching.parse import parse  # noqa: E402
from color_batching.pipeline import materials  # noqa: E402
from color_batching.plan import Options, plan  # noqa: E402
from color_batching.staggered import plan_staggered  # noqa: E402
from color_batching.tower import compensate  # noqa: E402
from color_batching.writer import Writer  # noqa: E402

NOTES = ["COLORSTACK writer comparison"]


def b(v):
    return "None" if v is None else struct.pack(">d", float(v)).hex()


def s(v):
    return "None" if v is None else str(v)


def write_path(w, p):
    w("\t".join(["P", s(p.tool), p.type, b(p.width), b(p.z), b(p.accel), s(p.obj), str(p.seq), str(int(p.retract_before)),
                 str(int(p.hop_before)), str(int(p.head)), str(int(p.tail)), s(p.tower), str(p.block), b(p.scv),
                 b(p.temp), b(p.height)]) + "\n")
    w("pts\t" + "\t".join(f"{b(x)},{b(y)}" for x, y in p.pts) + "\n")
    w("e\t" + "\t".join(b(v) for v in p.e) + "\n")
    w("f\t" + "\t".join(b(v) for v in p.f) + "\n")
    w("zs\t" + ("\t".join(b(v) for v in p.zs) if p.zs else "None") + "\n")
    for k, v in p.fans.items():
        w(f"fan\t{k}\t{v}\n")
    for k, v in p.vlim:
        w(f"vlim\t{k}\t{v}\n")
    for (word, names), line in p.state:
        w(f"st\t{word}\t{','.join(names)}\t{line}\n")
    for it in p.lead:
        if it[0] == "move":
            w("lm\t" + "\t".join(b(v) for v in it[1:]) + "\n")
        elif it[0] == "accel":
            w(f"la\t{b(it[1])}\n")
        else:
            w(f"ll\t{it[1]}\n")
    w("end\n")


if __name__ == "__main__":
    src, bands_out, expected = sys.argv[1:4]
    per_object = "per_object" in sys.argv[4:]
    staggered = "staggered" in sys.argv[4:]
    cfg = apply(copy.deepcopy(DEFAULTS), {"nozzle": {"contact": 0.2}})
    nz, st, co = cfg["nozzle"], cfg["stack"], cfg["cooling"]
    nozzle = Nozzle(nz["tip_diameter"] / 2, nz["cone_angle"], nz["clear_height"], nz["contact"])
    prog = parse(src)
    opts = Options(int(st["max_band_layers"]), st["surface_tolerance"], st["resolution"], st["min_piece"], per_object,
                   prog.free_support if st["free_support"] else frozenset(), materials(prog))
    if staggered:
        bands, _ = plan_staggered(prog, nozzle, opts)
    else:
        bands = plan(prog, nozzle, opts)
        compensate(prog, bands, nozzle)
    with open(bands_out, "w", encoding="utf-8", newline="\n") as f:
        w = f.write
        w(f"bands\t{len(bands)}\n")
        for band in bands:
            w("band\t" + "\t".join(str(L.index) for L in band.layers) + "\n")
            w("order\t" + "\t".join(f"{t}\t{s(o)}" for t, o in band.order) + "\n")
            for j, items in enumerate(band.work):
                w(f"layer\t{j}\t{len(items)}\n")
                for (t, o), p in items:
                    w(f"W\t{t}\t{s(o)}\n")
                    write_path(w, p)
    writer = Writer(prog, nozzle, cfg["swap"]["lift"], co["stacked_fan"], co["lift_wait"], co["min_layer_time"],
                    co["tower_between"], co["respect_min_layer_time"])
    lines = writer.write(bands, NOTES)
    Path(expected).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

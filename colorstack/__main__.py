"""python -m colorstack input.gcode [-o output.gcode] [-c config.toml] [--set section.key=value ...]"""
import argparse
import sys
import time
from pathlib import Path

from .pipeline import CONFIG_PATH, apply, load_config, run
from .estimate import fmt_time


def main():
    ap = argparse.ArgumentParser(prog="colorstack")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("-c", "--config", default=str(CONFIG_PATH))
    ap.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
                    help="override a setting, e.g. --set nozzle.cone_angle=40")
    a = ap.parse_args()
    values = {}
    for item in a.set:
        key, value = item.split("=", 1)
        section, name = key.split(".", 1)
        values.setdefault(section, {})[name] = value
    cfg = apply(load_config(a.config), values)
    if cfg["nozzle"]["measured"] is not True:
        print("WARNING: nozzle geometry is not marked measured; collision checks use placeholder values",
              file=sys.stderr)
    out = a.output or str(Path(a.input).with_suffix("")) + ".colorstack.gcode"
    t0 = time.time()
    r = run(a.input, out, cfg)
    print(f"non-planar ramped paths {r['ramped_paths']}, tower wipes kept {r['tower_wipes_kept']}, "
          f"turned into grid {r['tower_wipes_to_grid']}")
    print(f"cooling: {r['cooled_groups']} stacked regions slowed to the minimum layer time"
          + (f", {r['cooled_short']} still short at minimum speed" if r['cooled_short'] else "")
          + f"; fan raised on {r['fanned_paths']} lines" + (f"; {r['waits']} lift-and-waits" if r['waits'] else "")
          + f"; per-object batching {'used' if r['per_object'] else 'not used'}")
    print(f"bands {r['bands']}, ceded {r['ceded_mm']:.0f} mm ({r['visible_ceded_mm']:.0f} mm visible)")
    print(f"{'':10}{'original':>12}{'colorstack':>12}")
    for k, label in (("swaps", "swaps"), ("motion_s", "motion"), ("swap_s", "swapping"), ("total_s", "total")):
        b, f = r["original"][k], r["processed"][k]
        fmtv = str if k == "swaps" else fmt_time
        print(f"{label:10}{fmtv(b):>12}{fmtv(f):>12}")
    o = r["original"]
    print(f"times anchored to the {o['source']}" + f" ({fmt_time(o['swap_each_s'])} per colour change)")
    print(f"wrote {out} in {time.time() - t0:.1f}s")


main()

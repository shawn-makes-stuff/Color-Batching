"""Compare the cooling options on reference files: spot-layers cooled worse than the original (and under the minimum
layer time), print time, and outer wall mm printed at another speed than sliced (visible lines).
python tools/cooling_options.py [file ...]"""
import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from color_batching.config import DEFAULTS, apply  # noqa: E402
from color_batching.pipeline import run  # noqa: E402
from color_batching.verify import cooling_fidelity, fidelity  # noqa: E402

OPTIONS = {
    "off (default now)": {},
    "slow, outer walls kept": {"respect_min_layer_time": True},
    "slow + tower between": {"respect_min_layer_time": True, "tower_between": True},
    "slow + lift and wait": {"respect_min_layer_time": True, "lift_wait": True},
    "slow + tower + wait": {"respect_min_layer_time": True, "tower_between": True, "lift_wait": True},
}
FILES = ["samples/cube1h43.gcode", "work/cube_34m_print_1h2m_flush.gcode"]

if __name__ == "__main__":
    out = Path(tempfile.mkdtemp()) / "out.gcode"
    for f in sys.argv[1:] or FILES:
        for name, cooling in OPTIONS.items():
            cfg = apply(copy.deepcopy(DEFAULTS), {"nozzle": {"contact": 0.2}, "cooling": cooling})
            r = run(str(ROOT / f), str(out), cfg)
            n, bad, _ = cooling_fidelity(str(ROOT / f), str(out))
            wall = sum(mm for (field, group, typ, _), mm in fidelity(str(ROOT / f), str(out))["diffs"].items()
                       if field == "speed" and group == "kept" and typ == "Outer wall")
            print(f"{Path(f).stem[:26]:26} {name:24} cooled worse {100 * bad / max(n, 1):5.1f}%  "
                  f"time {r['processed']['total_s'] / 60:6.1f} min  outer wall at another speed {wall:7.0f} mm",
                  flush=True)

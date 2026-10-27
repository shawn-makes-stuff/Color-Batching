"""Seconds per pipeline stage (from the progress callback), no profiler: python tools/stage_times.py file ..."""
import copy
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colorstack.config import DEFAULTS, apply  # noqa: E402
from colorstack.pipeline import run  # noqa: E402

if __name__ == "__main__":
    out = str(Path(tempfile.mkdtemp()) / "o.gcode")
    cfg = apply(copy.deepcopy(DEFAULTS), {"nozzle": {"contact": 0.2}})
    for f in sys.argv[1:]:
        marks = []
        t0 = time.time()
        run(f, out, cfg, lambda frac, stage: marks.append((time.time() - t0, stage)))
        total = time.time() - t0
        spent, last_t, last_s = {}, 0.0, "start"
        for t, s in marks + [(total, "end")]:
            spent[last_s] = spent.get(last_s, 0.0) + t - last_t
            last_t, last_s = t, s
        print(f"{Path(f).stem[:30]:30} total {total:6.1f}s  " +
              "  ".join(f"{s}: {v:.1f}" for s, v in spent.items() if v > 0.05), flush=True)

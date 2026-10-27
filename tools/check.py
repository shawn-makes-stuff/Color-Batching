"""Quality report for a regression run: per output, against its original.

python tools/check.py LABEL [--collisions]
Per file: mm printed with a different state than the original on lines that kept their tool (fans raised by the
stacked-region rule left out: intended), per field; swaps with a block the original never used; filament left
retracted at the end (original -> output); with --collisions, nozzle / travel collisions (slow).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colorstack.config import DEFAULTS  # noqa: E402
from colorstack.verify import (arrival_fidelity, check, cooling_fidelity, e_balance, fidelity,  # noqa: E402
                               swap_fidelity)

from regress import OUT, SETTINGS  # noqa: E402


def report(label, collisions=False):
    runs = json.loads((OUT / f"{label}.json").read_text())
    nozzle = {**DEFAULTS["nozzle"], **SETTINGS["nozzle"]}
    for src in runs:
        out = OUT / label / (Path(src).stem + ".gcode")
        f = fidelity(str(ROOT / src), str(out))
        kept = {}
        for (field, group, typ, way), mm in f["diffs"].items():
            if group == "kept" and not (field == "fan" and way == "+"):
                kept[field] = kept.get(field, 0.0) + mm
        bad = sum(r[1] + r[3] for r in swap_fidelity(str(ROOT / src), str(out)).values())
        row = (f"{Path(src).stem[:34]:34} kept-line diffs: "
               + (", ".join(f"{k} {v:.0f} mm" for k, v in sorted(kept.items(), key=lambda kv: -kv[1])) or "none")
               + f" | wrong swap blocks {bad} | left retracted {e_balance(str(ROOT / src)):.2f} -> "
               f"{e_balance(str(out)):.2f}")
        c = arrival_fidelity(str(ROOT / src), str(out))
        row += (f" | arrivals {c['matched']} ({c['reordered']} reordered): retract {c['retract']} hop {c['hop']} "
                f"wipe {c['wipe']} "
                f"route {c['route']}")
        n, worse, _ = cooling_fidelity(str(ROOT / src), str(out))
        row += f" | cooled worse {100 * worse / max(n, 1):.1f}%"
        if collisions:
            problems, _, _ = check(str(ROOT / src), str(out), nozzle)
            kinds = {}
            for p in problems:
                kinds[p[0]] = kinds.get(p[0], 0) + 1
            row += f" | collisions {kinds or 0}"
        print(row, flush=True)


if __name__ == "__main__":
    report(sys.argv[1], "--collisions" in sys.argv)

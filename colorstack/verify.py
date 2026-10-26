"""Independent check of a processed file, replaying every move against a height map of the plastic so far:
nozzle cone and clear height on extrusion, tip on travel, late support, extrusion totals, swap clearance and
outer surface colours.  python -m colorstack.verify original.gcode processed.gcode [--contact X]
"""
import argparse
import math
import re

import numpy as np

from .config import CONFIG_PATH, load_config
from .reader import load, read


def moves(path):
    """[(x0, y0, z0, x1, y1, z1, de, width, in_layers, type)] for every linear piece."""
    return list(_moves(path))


def _moves(path):
    width, typ, in_layers = 0.45, "", False
    for i, kind, v in read(load(path)):
        if kind == "width":
            width = v
        elif kind == "type":
            typ = v
            if v == "Custom" and in_layers:
                in_layers = False       # end G-code
        elif kind == "layer":
            in_layers = True
        elif kind == "cmd" and v.startswith("SET_PRINT_STATS_INFO"):
            in_layers = True
        elif kind == "comment" and v.startswith("; COLORSTACK"):
            in_layers = True        # our output: layers follow the summary
        elif kind == "move":
            yield (v.x0, v.y0, v.z0, v.x1, v.y1, v.z1, v.de, width, in_layers, typ)


def swap_clearance(path):
    """Nozzle height above the tallest plastic so far at every tool change; must be positive."""
    top, z, out = 0.0, 0.0, []
    started = False
    for i, kind, v in read(load(path)):
        if kind == "move":
            z = v.z1
            if v.de > 0 and v.xy > 1e-9:
                top = max(top, v.z0, v.z1)
                started = True
        elif kind == "tool" and started:
            out.append(round(z - top, 3))
    return out


def surface_colours(original, processed, res=0.1):
    """Every outer-wall spot of the original with open air on one side must get the same tool in the processed
    file. Returns (surface mm checked, surface mm in a different colour)."""
    def tooled(path):
        tool, typ = 0, ""
        for i, kind, v in read(load(path)):
            if kind == "tool":
                tool = v[0]
            elif kind == "type":
                typ = v
            elif kind == "move" and v.de > 0 and v.xy > 1e-9 and typ != "Prime tower":
                yield tool, typ, v
    occ = {}                       # (z, cell) occupied in the original
    walls = []
    for tool, typ, v in tooled(original):
        z = round(v.z1, 2)
        n = max(1, int(v.xy / res))
        for t in np.linspace(0, 1, n + 1):
            c = (z, int(round((v.x0 + (v.x1 - v.x0) * t) / res)), int(round((v.y0 + (v.y1 - v.y0) * t) / res)))
            for ox in (-2, -1, 0, 1, 2):          # the line's width (about +-0.2 mm), not just its centre
                for oy in (-2, -1, 0, 1, 2):
                    occ.setdefault((z, c[1] + ox, c[2] + oy), tool)
            if typ in ("Outer wall", "Overhang wall"):
                walls.append((c, v, tool))
    def free(z, x, y):
        return all((z, int(round((x + dx) / res)), int(round((y + dy) / res))) not in occ
                   for dx in (-0.1, 0, 0.1) for dy in (-0.1, 0, 0.1))
    surface = {}
    for c, v, tool in walls:
        dx, dy = v.x1 - v.x0, v.y1 - v.y0
        n = math.hypot(dx, dy) or 1
        nx, ny = -dy / n, dx / n
        x, y = c[1] * res, c[2] * res
        if free(c[0], x + nx * 0.6, y + ny * 0.6) or free(c[0], x - nx * 0.6, y - ny * 0.6):
            surface[c] = tool
    got = {}
    for tool, typ, v in tooled(processed):
        z = round(v.z1 if abs(v.z1 - v.z0) < 1e-6 else -1, 2)
        n = max(1, int(v.xy / res))
        for t in np.linspace(0, 1, n + 1):
            c = (z, int(round((v.x0 + (v.x1 - v.x0) * t) / res)), int(round((v.y0 + (v.y1 - v.y0) * t) / res)))
            if c in surface:
                got.setdefault(c, tool)
    wrong = sum(1 for c, t in surface.items() if c in got and got[c] != t)
    return len(surface) * res, wrong * res


def _states(path, step=0.5):
    """Samples every `step` mm along each extrusion, with the machine state it printed under:
    {(z, x, y): (tool, type, F, flow mm/mm, PA, fan, other fans, nozzle temp, accel, speed %, flow %)}, and the number
    of extrusion starts per feature type (an extrusion right after a travel or a retract)."""
    out, starts = {}, {}
    tool, typ, pa, temp, accel, speed, flow = 0, "", None, None, None, 100.0, 100.0
    fans = {}
    live = moving = False
    lines = load(path)
    # the first layer's temperature from the settings, as the parser does (start G-code may set it by macro)
    m = next((re.match(r"^; nozzle_temperature_initial_layer = (.*)$", ln) for ln in lines
              if ln.startswith("; nozzle_temperature_initial_layer =")), None)
    initial = [float(x) for x in m.group(1).split(",")] if m else []
    for i, kind, v in read(lines):
        if kind == "tool":
            tool = v[0]
            if not live and initial:
                temp = initial[min(tool, len(initial) - 1)]
        elif kind == "type":
            typ = v
            if v == "Custom" and live:
                live = False
        elif kind == "layer" or kind == "comment" and v.startswith("; COLORSTACK"):
            live = True        # our output: layers follow the summary
        elif kind == "accel":
            accel = v
        elif kind == "cmd":
            w = v.split()[0].upper()
            words = dict(re.findall(r"\b([A-Z])(-?\d*\.?\d+)", v.upper()))
            if w == "SET_PRESSURE_ADVANCE" and "ADVANCE=" in v.upper():
                pa = float(re.search(r"ADVANCE=(-?\d*\.?\d+)", v.upper()).group(1))
            elif w in ("M900", "M572"):
                pa = float(words.get("K", words.get("S", pa or 0)))
            elif w in ("M104", "M109") and "S" in words and ("T" not in words or int(float(words["T"])) == tool):
                temp = float(words["S"])
            elif w in ("M106", "M107"):
                key = "" if words.get("P", "0") == "0" else "P" + words["P"]
                fans[key] = 0 if w == "M107" else int(float(words.get("S", 255)))
            elif w == "M220" and "S" in words:
                speed = float(words["S"])
            elif w == "M221" and "S" in words:
                flow = float(words["S"])
        elif kind == "move":
            extruding = v.de > 0 and v.xy > 1e-9
            if not extruding:
                if v.xy > 1e-9 or v.de != 0 or abs(v.z1 - v.z0) > 1e-9:   # not a feedrate-only line
                    moving = False
                continue
            if not live or typ == "Prime tower":
                continue
            if not moving:
                starts[typ] = starts.get(typ, 0) + 1
                moving = True
            n = max(1, int(v.xy / step))
            other = tuple(sorted((k, s) for k, s in fans.items() if k))
            state = (tool, typ, round(v.f), round(v.de / v.xy, 4), pa, fans.get("", 0), other, temp, accel, speed, flow)
            for t in (np.arange(n) + 0.5) / n:
                key = (round(v.z0 + (v.z1 - v.z0) * t, 2), round((v.x0 + (v.x1 - v.x0) * t) / 0.1),
                       round((v.y0 + (v.y1 - v.y0) * t) / 0.1))
                out.setdefault(key, state)
    return out, starts


def _travels(path):
    """Travel style within the layers: retracts, wipes, Z hops, firmware retracts (G10), travel mm."""
    out = dict.fromkeys(("retracts", "wipes", "hops", "G10", "travel_mm"), 0.0)
    live = False
    for i, kind, v in read(load(path)):
        if kind == "layer" or kind == "comment" and v.startswith("; COLORSTACK"):
            live = True
        elif kind == "type" and v == "Custom" and live:
            live = False
        elif not live:
            continue
        elif kind == "comment" and v.startswith(";WIPE_START"):
            out["wipes"] += 1
        elif kind == "cmd" and v.split()[0].upper() == "G10":
            out["G10"] += 1
        elif kind == "move":
            if v.de < 0 and v.xy < 1e-9:
                out["retracts"] += 1
            if v.z1 > v.z0 + 1e-6 and v.de == 0:
                out["hops"] += 1
            if v.de == 0:
                out["travel_mm"] += v.xy
    return out


FIELDS = ("tool", "type", "speed", "flow", "PA", "fan", "other fans", "temperature", "accel", "speed %", "flow %")


def fidelity(original, processed, step=0.5):
    """How the processed file prints the original's extrusions: per state field, the mm printed differently, split
    into lines that kept their tool and lines handed to another tool; and extrusion starts per feature type."""
    a, starts_a = _states(original, step)
    b, starts_b = _states(processed, step)
    near = [(0, 0)] + [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
    diffs = {}          # (field, kept | handed, type, + | - | "") -> mm
    examples = {}       # same key -> ((z, x, y), original value, processed value), the first one found
    matched = missing = 0
    for (z, x, y), sa in a.items():
        sb = next((b[(z, x + dx, y + dy)] for dx, dy in near if (z, x + dx, y + dy) in b), None)
        if sb is None:
            missing += 1
            continue
        matched += 1
        group = "kept" if sa[0] == sb[0] else "handed"
        for k in range(1, len(FIELDS)):
            same = (abs(sa[k] - sb[k]) <= 0.01 * max(abs(sa[k]), 1e-9) if isinstance(sa[k], float) and
                    isinstance(sb[k], float) else sa[k] == sb[k])
            if not same:
                way = ("+" if sb[k] > sa[k] else "-") if isinstance(sa[k], (int, float)) and isinstance(
                    sb[k], (int, float)) else ""
                key = (FIELDS[k], group, sa[1], way)
                diffs[key] = diffs.get(key, 0.0) + step
                examples.setdefault(key, ((z, x * 0.1, y * 0.1), sa[k], sb[k]))
    return {"matched_mm": matched * step, "missing_mm": missing * step, "diffs": diffs, "examples": examples,
            "starts": {t: (starts_a.get(t, 0), starts_b.get(t, 0)) for t in starts_a.keys() | starts_b.keys()},
            "travels": (_travels(original), _travels(processed))}


def check(original, processed, nozzle, res=0.05):
    proc = moves(processed)
    pts = [(m[3], m[4]) for m in proc if m[6] > 0 and m[8]]
    a = np.asarray(pts)
    x0, y0 = a.min(0) - 5
    x1, y1 = a.max(0) + 5
    H = np.full((int((y1 - y0) / res) + 1, int((x1 - x0) / res) + 1), -1.0)
    tan = math.tan(math.radians(nozzle["cone_angle"]))
    tip = nozzle["tip_diameter"] / 2
    contact = nozzle.get("contact", 0.0)
    rmax = tip + nozzle["clear_height"] * tan
    k = int(math.ceil(max(rmax, 3.0) / res))
    oy, ox = np.mgrid[-k:k + 1, -k:k + 1]
    dist = np.hypot(ox, oy) * res

    def window(cx, cy):
        c, r = int(round((cx - x0) / res)), int(round((cy - y0) / res))
        if r - k < 0 or c - k < 0 or r + k + 1 > H.shape[0] or c + k + 1 > H.shape[1]:
            return None          # off the area that holds any plastic: nothing to hit
        return H[r - k:r + k + 1, c - k:c + k + 1]

    problems = []
    floating = []
    e_total = 0.0
    # layer heights from the original: lines that change height put many other heights in a file
    zs = sorted({round(m[5], 3) for m in moves(original) if m[6] > 0 and m[8] and m[3:5] != m[0:2]})
    first_z = zs[0]
    layer_h = min((b - a for a, b in zip(zs, zs[1:]) if b - a > 0.05), default=0.2)
    printed = {}   # (z, cell) -> index of the extrusion that printed it, for the late-support check
    order = 0
    for (ax, ay, az, bx, by, bz, de, w, live, _typ) in proc:
        if not live:
            if de > 0:
                e_total += de
            continue
        seg = math.hypot(bx - ax, by - ay)
        n = max(1, int(seg / (res * 4)))
        if de > 0 and seg > 0:
            e_total += de
            order += 1
            for t in np.linspace(0, 1, n + 1):
                cx, cy = ax + (bx - ax) * t, ay + (by - ay) * t
                printed.setdefault((round(bz, 2), int(round((cy - y0) / res / 4)), int(round((cx - x0) / res / 4))), order)
            for t in np.linspace(0, 1, n + 1)[1:]:
                cx, cy = ax + (bx - ax) * t, ay + (by - ay) * t
                cz = az + (bz - az) * t           # a line may change height along the segment
                win = window(cx, cy)
                if win is None:
                    continue
                dz = win - cz - contact
                hit = (dz > 1e-3) & (dist < tip + dz * tan)
                hit |= (dz > nozzle["clear_height"]) & (dist < 3.0)
                if hit.any():
                    problems.append(("extrude", round(cx, 2), round(cy, 2), round(cz, 3), float(dz[hit].max())))
                # nothing under the line centre yet: remember it; it is only a problem if a lower layer is
                # printed there later (support that came too late), not a genuine overhang of the model
                below = H[int(round((cy - y0) / res)), int(round((cx - x0) / res))]
                if below < cz - 1.5 * layer_h and cz > first_z + 1e-3 and abs(bz - az) < 1e-6:
                    floating.append((cx, cy, cz, order))
                # lay the line: cells within w/2 of the nozzle centre get this line's top
                win[(dist <= w / 2) & (win < cz)] = cz
        elif seg > 0:
            zt = min(az, bz)
            for t in np.linspace(0, 1, n + 1):
                cx, cy = ax + (bx - ax) * t, ay + (by - ay) * t
                win = window(cx, cy)
                if win is None:
                    continue
                hit = (win > zt + contact + 1e-3) & (dist < tip)
                if hit.any():
                    problems.append(("travel", round(cx, 2), round(cy, 2), zt, float(win[hit].max() - zt)))
    # support printed after the line above it
    late = []
    for cx, cy, bz, when in floating:
        key = (round(bz - layer_h, 2), int(round((cy - y0) / res / 4)), int(round((cx - x0) / res / 4)))
        if printed.get(key, -1) > when:
            late.append(("late-support", round(cx, 2), round(cy, 2), bz, layer_h))
    problems.extend(late)

    def totals(ms):
        model = tower = 0.0
        for m in ms:
            if m[6] > 0 and m[3:5] != m[0:2]:
                if m[9] == "Prime tower":
                    tower += m[6]
                else:
                    model += m[6]
        return model, tower
    return problems, totals(_moves(original)), totals(proc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("original")
    ap.add_argument("processed")
    ap.add_argument("-c", "--config", default=str(CONFIG_PATH))
    ap.add_argument("--contact", type=float, help="override nozzle.contact")
    ap.add_argument("--set", action="append", default=[], metavar="nozzle.KEY=VALUE")
    ap.add_argument("--fidelity", action="store_true", help="only compare print state per extruded mm")
    a = ap.parse_args()
    if a.fidelity:
        r = fidelity(a.original, a.processed)
        print(f"matched {r['matched_mm']:.0f} mm, not found {r['missing_mm']:.0f} mm")
        for key, mm in sorted(r["diffs"].items(), key=lambda kv: -kv[1]):
            field, group, typ, way = key
            (z, x, y), va, vb = r["examples"][key]
            print(f"  {field:12} {way:1} {group:7} {typ:24} {mm:10.1f} mm   e.g. z{z} x{x:.1f} y{y:.1f}: {va} -> {vb}")
        print("extrusion starts (original -> processed):")
        for typ, (x, y) in sorted(r["starts"].items()):
            print(f"  {typ:28} {x:6} -> {y:6}")
        ta, tb = r["travels"]
        print("travels: " + ", ".join(f"{k} {ta[k]:.0f} -> {tb[k]:.0f}" for k in ta))
        return
    nozzle = load_config(a.config)["nozzle"]
    if a.contact is not None:
        nozzle["contact"] = a.contact
    for item in a.set:
        key, value = item.split("=", 1)
        nozzle[key.split(".", 1)[1]] = float(value)
    problems, eo, ep = check(a.original, a.processed, nozzle)
    print(f"model extrusion: original {eo[0]:.2f} mm, processed {ep[0]:.2f} mm, diff {ep[0] - eo[0]:+.3f}")
    print(f"prime tower:     original {eo[1]:.2f} mm, processed {ep[1]:.2f} mm, diff {ep[1] - eo[1]:+.3f}")
    kinds = {}
    for p in problems:
        kinds.setdefault(p[0], []).append(p)
    print(f"collisions: {len(problems)}", {k: len(v) for k, v in kinds.items()})
    gaps = swap_clearance(a.processed)
    if gaps:
        low = sum(1 for g in gaps if g <= 0)
        print(f"swap clearance: {len(gaps)} tool changes, lowest {min(gaps):+.2f} mm above the tallest plastic"
              f"{'' if not low else f', {low} at or below it'}")
    total, wrong = surface_colours(a.original, a.processed)
    print(f"outer surface colour: {wrong:.1f} mm of {total:.0f} mm printed in a different colour than the original")
    for k, v in kinds.items():
        worst = sorted(v, key=lambda p: -p[4])[:5]
        print(f"  worst {k}:", worst)


if __name__ == "__main__":
    main()

"""Print-time estimate, the same for the original and the processed file.

Motion follows Klipper's planner (trapezoids, lookahead, square-corner junctions) with the machine limits the
slicer wrote into the file. Colour changes are not modelled: the per-swap cost is whatever the slicer's own
total leaves after our motion time.
"""
import math
import re
from dataclasses import dataclass

from .model import first_setting
from .reader import read


@dataclass
class Limits:
    v: float
    a: float
    scv: float
    zv: float
    za: float
    swap_s: float   # the profile's load + unload time, only for files without a slicer total


def machine_limits(settings, fallback):
    """The file's own limits (first value = normal mode); the [printer] fallback where it has none."""
    def first(key, default):
        return first_setting(settings, key, default)
    return Limits(min(first("machine_max_speed_x", fallback["max_velocity"]),
                      first("machine_max_speed_y", fallback["max_velocity"])),
                  first("machine_max_acceleration_extruding", fallback["max_accel"]),
                  first("machine_max_jerk_x", fallback["square_corner_velocity"]),
                  first("machine_max_speed_z", fallback["max_z_velocity"]),
                  first("machine_max_acceleration_z", fallback["max_z_accel"]),
                  first("machine_unload_filament_time", 0.0) + first("machine_load_filament_time", 0.0))


def parse_duration(text):
    """'1d 2h 3m 4s' -> seconds."""
    return sum(int(v) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u] for v, u in re.findall(r"(\d+)\s*([dhms])", text))


def fmt_duration(seconds):
    """Seconds -> '1h 2m 3s', as the slicers write it."""
    h, r = divmod(int(round(seconds)), 3600)
    m, s = divmod(r, 60)
    return (f"{h}h " if h else "") + f"{m}m {s}s"


def file_estimate(lines):
    """The slicer's total in seconds: '; estimated printing time (normal mode) = 1h 2m 3s' or Cura ';TIME:123'."""
    for ln in lines:
        m = re.match(r"^;\s*estimated printing time(?: \(normal mode\))?\s*=\s*(.+)$", ln.strip())
        if m:
            return parse_duration(m.group(1)) or None
        m = re.match(r"^;TIME:(\d+)", ln.strip())
        if m:
            return float(m.group(1))
    return None


def swap_each(o, slicer, lim):
    """(seconds per swap, source): (slicer - our motion and dwell of original o) / swaps, else the profile's."""
    if slicer:
        return (max(0.0, slicer - o["motion_s"] - o["dwell_s"]) / o["swaps"] if o["swaps"] else 0.0,
                "slicer estimate")
    return lim.swap_s, "Color Batching model"


def anchor(o, p, slicer, lim):
    """Totals for original o and processed p: original = the slicer's figure, the rest per swap_each."""
    each, source = swap_each(o, slicer, lim)
    for r in (o, p):
        r["swap_s"] = r["swaps"] * each
        r["total_s"] = r["motion_s"] + r["dwell_s"] + r["swap_s"]
        r["source"], r["swap_each_s"] = source, each
    if slicer:
        o["total_s"] = slicer
        p["total_s"] = (slicer + (p["motion_s"] - o["motion_s"]) + (p["dwell_s"] - o["dwell_s"])
                        - (o["swaps"] - p["swaps"]) * each)


def _flush(moves, scv, steps=None):
    """Time of moves [(dist, vmax, accel, unit vector or None, line)]; per-move (line, s) into steps."""
    n = len(moves)
    if not n:
        return 0.0
    jv2 = [0.0] * (n + 1)          # max speed^2 at the junction before move k
    for k in range(1, n):
        d0, v0, a0, u0, _ = moves[k - 1]
        d1, v1, a1, u1, _ = moves[k]
        if u0 is None or u1 is None:
            continue
        cos_t = -(u0[0] * u1[0] + u0[1] * u1[1] + u0[2] * u1[2])
        lim2 = min(v0 * v0, v1 * v1)
        if cos_t < -0.999999:      # straight on
            jv2[k] = lim2
            continue
        sin_d2 = math.sqrt(max(0.0, 0.5 * (1.0 - cos_t)))
        if sin_d2 > 0.999999:      # full reversal
            continue
        a = min(a0, a1)
        jd = scv * scv * (math.sqrt(2) - 1) / a
        jv2[k] = min(jd * a * sin_d2 / (1.0 - sin_d2), lim2)
    end2 = [0.0] * n               # backward pass: brake in time for the next junction
    nxt = 0.0
    for k in range(n - 1, -1, -1):
        d, v, a, u, _ = moves[k]
        end2[k] = min(jv2[k + 1], nxt)
        nxt = min(jv2[k], end2[k] + 2 * a * d, v * v)
    t = 0.0
    cur = 0.0
    for k in range(n):
        d, v, a, u, line = moves[k]
        s2 = min(cur, jv2[k], v * v) if k else 0.0
        e2 = min(end2[k], s2 + 2 * a * d, v * v)
        vc2 = max(min(v * v, a * d + (s2 + e2) / 2), s2, e2)
        vs, vc, ve = math.sqrt(s2), math.sqrt(vc2), math.sqrt(e2)
        dc = max(0.0, d - (vc2 - s2) / (2 * a) - (vc2 - e2) / (2 * a))
        tk = (vc - vs) / a + (vc - ve) / a + (dc / vc if vc > 0 else 0.0)
        t += tk
        if steps is not None:
            steps.append((line, tk))
        cur = e2
    return t


def estimate(lines, lim, steps=None):
    """Motion, dwell and swap count of a file, and per tool the filament extruded and the swaps into it.
    steps collects the timeline: (line, s) per move and dwell, (line, None) per colour change."""
    accel, scv = lim.a, lim.scv
    moves = []
    motion = dwell = 0.0
    swaps = 0
    tool = None
    extruded, swaps_into = {}, {}
    for i, kind, v in read(lines):
        if kind == "move":
            extruded[tool] = extruded.get(tool, 0.0) + v.de
            dx, dy, dz = v.x1 - v.x0, v.y1 - v.y0, v.z1 - v.z0
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            if d > 1e-9:
                vmax = min(v.f / 60, lim.v)
                a = accel
                if abs(dz) > 1e-9:
                    ratio = d / abs(dz)
                    vmax = min(vmax, lim.zv * ratio)
                    a = min(a, lim.za * ratio)
                moves.append((d, vmax, a, (dx / d, dy / d, dz / d), i))
            elif v.de != 0:
                moves.append((abs(v.de), v.f / 60, 5000.0, None, i))   # retract / deretract
            continue
        if kind == "accel":
            accel = min(v, lim.a)
            continue
        w = v.split()[0].upper() if kind == "cmd" else None
        if kind not in ("scv", "tool") and w not in ("G4", "M400", "PRINT_END"):
            continue
        motion += _flush(moves, scv, steps)
        moves = []
        if kind == "scv":
            scv = v
        elif kind == "tool":
            if tool is not None and v[0] != tool:
                swaps += 1
                swaps_into[v[0]] = swaps_into.get(v[0], 0) + 1
                if steps is not None:
                    steps.append((i, None))
            tool = v[0]
        elif w == "G4":
            m = re.search(r"([PS])(\d*\.?\d+)", v[2:])
            if m:
                s = float(m.group(2)) / (1000 if m.group(1) == "P" else 1)
                dwell += s
                if steps is not None:
                    steps.append((i, s))
    motion += _flush(moves, scv, steps)
    return {"motion_s": motion, "dwell_s": dwell, "swaps": swaps, "extruded": extruded, "swaps_into": swaps_into}


def fmt_time(s):
    h, r = divmod(int(round(s)), 3600)
    m, sec = divmod(r, 60)
    return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m}m{sec:02d}s"

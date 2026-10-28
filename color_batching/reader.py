"""One G-code reader for the parser, the estimate and the checker.

read() yields (line_index, kind, payload):
  "move" Move (arcs linearized, de always relative), "tool" (n, line), "accel" a, "scv" v, "layer" None,
  "z" z, "height" h, "type" name (Orca names), "width" w, "cmd" line (comment stripped), "comment" line
"""
import math
import re
from dataclasses import dataclass

WORD_RE = re.compile(r"([A-Z])\s*(-?\d*\.?\d+)")
TOOL_RE = re.compile(r"^T(\d+)\b")

# Cura / Prusa feature names -> Orca names
CURA_TYPES = {"WALL-OUTER": "Outer wall", "WALL-INNER": "Inner wall", "SKIN": "Top surface", "FILL": "Sparse infill",
              "SUPPORT": "Support", "SUPPORT-INTERFACE": "Support interface", "SKIRT": "Skirt",
              "PRIME-TOWER": "Prime tower", "TRAVEL": "Travel"}
OTHER_TYPES = {"External perimeter": "Outer wall", "Perimeter": "Inner wall", "Overhang perimeter": "Overhang wall",
               "Internal infill": "Sparse infill", "Solid infill": "Internal solid infill",
               "Top solid infill": "Top surface", "Bridge infill": "Bridge", "Wipe tower": "Prime tower",
               "Support material": "Support", "Support material interface": "Support interface"}


@dataclass
class Move:
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float
    de: float      # mm of filament, negative = retract
    f: float       # mm/min
    word: str

    @property
    def xy(self):
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


def load(path):
    return open(path, encoding="utf-8", errors="replace").read().splitlines()


def arc_points(x0, y0, x1, y1, i, j, clockwise, tol=0.01):
    """Points along an arc around (x0+i, y0+j), start excluded, chord error below tol."""
    cx, cy = x0 + i, y0 + j
    r = math.hypot(i, j)
    if r < 1e-9:
        return [(x1, y1)]
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    sweep = a1 - a0
    if clockwise and sweep >= 0:
        sweep -= 2 * math.pi
    elif not clockwise and sweep <= 0:
        sweep += 2 * math.pi
    step = 2 * math.acos(max(-1.0, min(1.0, 1 - tol / r))) if r > tol else math.pi / 2
    n = max(1, math.ceil(abs(sweep) / max(step, 1e-6)))
    pts = [(cx + r * math.cos(a0 + sweep * k / n), cy + r * math.sin(a0 + sweep * k / n)) for k in range(1, n)]
    pts.append((x1, y1))
    return pts


def _centre_from_r(x0, y0, x1, y1, r, clockwise):
    """I/J for an R-form arc (negative R = the long way round)."""
    dx, dy = x1 - x0, y1 - y0
    d = math.hypot(dx, dy)
    if d < 1e-9 or abs(r) < d / 2 - 1e-9:
        return None
    h = math.sqrt(max(0.0, r * r - d * d / 4))
    mx, my = x0 + dx / 2, y0 + dy / 2
    nx, ny = -dy / d, dx / d
    side = (1 if clockwise else -1) * (1 if r > 0 else -1)
    cx, cy = mx - side * nx * h, my - side * ny * h
    return cx - x0, cy - y0


def _words(code):
    return {k: float(n) for k, n in WORD_RE.findall(code.upper())}


def read(lines):
    x = y = z = 0.0
    e = 0.0
    f = 3000.0
    abs_xyz, abs_e = True, False
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if line[0] == ";":
            for kind, payload in _comment(line):
                yield idx, kind, payload
            continue
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        word = code.split()[0].upper()
        if word in ("G0", "G1", "G2", "G3"):
            v = _words(code[len(word):])
            if "F" in v:
                f = v["F"]
            nx = (v["X"] if abs_xyz else x + v["X"]) if "X" in v else x
            ny = (v["Y"] if abs_xyz else y + v["Y"]) if "Y" in v else y
            nz = (v["Z"] if abs_xyz else z + v["Z"]) if "Z" in v else z
            if "E" in v:
                # Klipper and Marlin both make E relative under G91, whatever M82/M83 say
                e_abs = abs_e and abs_xyz
                de = v["E"] - e if e_abs else v["E"]
                e = v["E"] if e_abs else e + v["E"]
            else:
                de = 0.0
            if word in ("G2", "G3") and ("I" in v or "J" in v or "R" in v):
                if "R" in v and not ("I" in v or "J" in v):
                    ij = _centre_from_r(x, y, nx, ny, v["R"], word == "G2")
                else:
                    ij = (v.get("I", 0.0), v.get("J", 0.0))
                pts = arc_points(x, y, nx, ny, ij[0], ij[1], word == "G2") if ij else [(nx, ny)]
                lens, px, py = [], x, y
                for qx, qy in pts:
                    lens.append(math.hypot(qx - px, qy - py))
                    px, py = qx, qy
                total = sum(lens) or 1.0
                px, py, pz = x, y, z
                for k, ((qx, qy), ln) in enumerate(zip(pts, lens)):
                    qz = z + (nz - z) * (k + 1) / len(pts)
                    yield idx, "move", Move(px, py, pz, qx, qy, qz, de * ln / total, f, word)
                    px, py, pz = qx, qy, qz
            else:
                yield idx, "move", Move(x, y, z, nx, ny, nz, de, f, word)
            x, y, z = nx, ny, nz
        elif word == "G90":
            abs_xyz = True
        elif word == "G91":
            abs_xyz = False
        elif word == "M82":
            abs_e = True
        elif word == "M83":
            abs_e = False
        elif word == "G92":
            v = _words(code[3:])
            if not v:
                e = 0.0
            if "E" in v:
                e = v["E"]
            x, y, z = v.get("X", x), v.get("Y", y), v.get("Z", z)
        elif word == "SET_VELOCITY_LIMIT":
            m = re.search(r"ACCEL=(\d*\.?\d+)", code, re.I)
            if m:
                yield idx, "accel", float(m.group(1))
            m = re.search(r"SQUARE_CORNER_VELOCITY=(\d*\.?\d+)", code, re.I)
            if m:
                yield idx, "scv", float(m.group(1))
            yield idx, "cmd", code
        elif word == "M204":
            v = _words(code[4:])
            a = v.get("S", v.get("P"))
            if a is not None:
                yield idx, "accel", a
            yield idx, "cmd", code
        else:
            m = TOOL_RE.match(code)
            if m:
                yield idx, "tool", (int(m.group(1)), code)
            else:
                yield idx, "cmd", code


LAYER_RE = re.compile(r"^;\s*LAYER:\s*-?\d+")
LAYER_WORD_RE = re.compile(r"^;\s*layer \d+", re.I)


def _comment(line):
    body = line[1:].strip()
    # both layer patterns need "layer" first after the ";" (the cheap test spares the regexes on other comments)
    if line.startswith(";LAYER_CHANGE") or (body[:5].lower() == "layer" and (LAYER_RE.match(line)
                                                                             or LAYER_WORD_RE.match(line))):
        yield "layer", None
        m = re.search(r"Z\s*=\s*(-?\d*\.?\d+)", line)
        if m:
            yield "z", float(m.group(1))
    elif line.startswith(";Z:"):
        yield "z", float(line[3:])
    elif line.startswith(";HEIGHT:"):
        yield "height", float(line[8:])
    elif line.startswith(";TYPE:"):
        name = line[6:].strip()
        yield "type", CURA_TYPES.get(name, OTHER_TYPES.get(name, name))
    elif body.startswith("FEATURE:"):
        name = body[8:].strip()
        yield "type", OTHER_TYPES.get(name, name)
    elif line.startswith(";WIDTH:"):
        yield "width", float(line[7:])
    else:
        yield "comment", line

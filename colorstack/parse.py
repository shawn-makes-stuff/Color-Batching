"""Parse multicolour G-code into layers of extrusion paths.

Travels, retracts, wipes and Z hops are dropped (the writer regenerates them in the file's own style); commands
the processor does not understand stay with their layer, in order.
"""
import math
import re
from collections import Counter

from .model import NEUTRAL_TYPES, Layer, Path, Program, first_setting
from .reader import load, read

# recomputed or meaningless after reordering
DROP = {"G92", "M73", "SET_PRINT_STATS_INFO", "G17", "M117", "SET_VELOCITY_LIMIT", "M204", "M82", "M83",
        "G90", "G91"}

TEMPS = {"M104", "M109", "M140", "M190", "M141", "M191"}
# machine state the slicer sets between features (Orca: per-filament and adaptive pressure advance): kept with each path
STATE = {"SET_PRESSURE_ADVANCE", "M900", "M572", "M220", "M221", "M205", "M566"}


def _parse_settings(lines):
    out = {}
    for ln in lines:
        m = re.match(r"^;\s*(\w+)\s*=\s*(.*)$", ln)
        if m:
            out.setdefault(m.group(1), m.group(2).strip())
    return out


def parse(path_in):
    lines = load(path_in)
    settings = _parse_settings(lines)
    header = []
    if lines and lines[0].startswith("; HEADER_BLOCK_START"):
        for ln in lines:
            header.append(ln)
            if ln.startswith("; HEADER_BLOCK_END"):
                break

    events = list(read(lines))
    markers = [i for i, k, _ in events if k == "layer"]
    extrude_idx = [i for i, k, m in events if k == "move" and m.de > 0 and m.xy > 1e-9]
    if not extrude_idx:
        raise ValueError("no extrusion moves found")
    first_layer = markers[0] if markers else extrude_idx[0]
    # end G-code: Orca/Prusa mark it ;TYPE:Custom after the layers; otherwise it starts after the last extrusion
    customs = [i for i, ln in enumerate(lines) if ln.startswith(";TYPE:Custom") and i > first_layer]
    last_extrusion = extrude_idx[-1]
    end = customs[-1] if customs and customs[-1] > last_extrusion else last_extrusion + 1
    preamble = lines[:first_layer]
    postamble = lines[end:]

    prog = Program(settings, preamble, postamble, [], 0, [], header)
    tool = 0
    fmt_set = accel_seen = False
    prog.stats_info = any(ln.startswith("SET_PRINT_STATS_INFO") for ln in lines)
    modes = [ln.split(";")[0].strip().upper() for ln in preamble]
    modes = [w for w in modes if w in ("M82", "M83")]
    prog.abs_e = bool(modes) and modes[-1] == "M82"
    layers, cur = [], None
    accel, scv, typ, width, obj, vlim = None, None, "", None, None, ()
    height = None
    # nozzle temperature: the first layer's from the settings (start G-code may set it by macro), else the start
    # G-code's own M104/M109
    pre_tools = [v[0] for i, k, v in events if k == "tool" and i < first_layer]
    initial = prog.setting_list("nozzle_temperature_initial_layer") or prog.setting_list("first_layer_temperature")
    temp = initial[min(pre_tools[-1] if pre_tools else 0, len(initial) - 1)] if initial else None
    fans = {}
    state = {}         # key -> line, see _state_key
    path = None
    seq = 0
    retracted = hopped = False
    in_toolchange = False
    tc_lines, toolchange_extra = [], None
    tower_sec, block = "wall", -1
    retracts, deretracts, hops, travels = [], [], [], []

    def new_layer():
        nonlocal cur
        cur = Layer(len(layers), 0.0, 0.0)
        layers.append(cur)

    def close():
        nonlocal path, seq, retracted, hopped
        if path is not None and path.e:
            cur.paths.append(path)
            seq += 1
            retracted = hopped = False
        path = None

    for i, kind, val in events:
        if i >= end:
            break
        live = i >= first_layer
        if kind == "move":
            m = val
            if not live:
                continue
            if not markers and m.de > 0 and m.xy > 1e-9 and (cur is None or m.z1 > cur.z + 1e-4):
                close()
                new_layer()
                cur.z = m.z1
            if cur is None:
                new_layer()
            if m.xy < 1e-9 and m.de == 0 and abs(m.z1 - m.z0) < 1e-9:
                continue       # feedrate-only line
            if m.de > 0 and m.xy > 1e-9 and abs(m.z1 - m.z0) >= 1e-9:
                raise ValueError(f"line {i + 1} extrudes while changing Z (spiral vase?): not supported")
            if m.de > 0 and m.xy > 1e-9 and abs(m.z1 - m.z0) < 1e-9:
                if path is None:
                    tl = None if typ in NEUTRAL_TYPES else tool
                    path = Path(tl, typ, width, m.z1, [(m.x0, m.y0)], [], [], accel, dict(fans), obj,
                                seq, retracted, hopped, scv=scv, temp=temp, vlim=vlim, height=height,
                                state=tuple(sorted(state.items())))
                    if typ in NEUTRAL_TYPES:
                        path.tower, path.block = tower_sec, block if tower_sec == "wipe" else -1
                path.pts.append((m.x1, m.y1))
                path.e.append(m.de)
                path.f.append(m.f)
            else:
                close()        # travel, Z move, retract or deretract
                if m.de < 0:
                    retracted = True
                    if m.xy < 1e-9:
                        retracts.append((round(-m.de, 3), m.f))
                elif m.de > 0 and m.xy < 1e-9:
                    deretracts.append(m.f)
                if m.z1 > m.z0 + 1e-9:
                    hopped = True
                    if m.xy < 1e-9:
                        hops.append(round(m.z1 - m.z0, 3))
                if m.xy > 1e-9 and m.de == 0:
                    travels.append(m.f)
            continue
        if kind == "tool":
            n, code = val
            if live:
                close()
                if not fmt_set:
                    fmt = re.sub(r"^T\d+", "T{n}", code)
                    prog.tool_format = re.sub(r"PURGE_LENGTH=\S+", "PURGE_LENGTH={purge}", fmt)
                    fmt_set = True
            else:
                prog.first_tool = n
            tool = n
            continue
        if kind == "accel":
            if live:
                close()
                if not accel_seen:
                    w = lines[i].split()[0].upper()
                    prog.accel_format = "M204 S{a}" if w == "M204" else "SET_VELOCITY_LIMIT ACCEL={a}"
                    accel_seen = True
            accel = val
            continue
        if kind == "scv":
            if live:
                close()
            scv = val
            continue
        if not live:
            w = val.split()[0].upper() if kind == "cmd" else None
            if w in ("M106", "M107"):
                _fan(fans, w, val)
            elif w in ("M104", "M109") and not initial:
                temp = _temp(val, tool, temp)
            continue
        if kind == "layer":
            close()
            new_layer()
        elif kind == "z":
            if cur is not None:
                cur.z = val
        elif kind == "height":      # Orca: after ;Z: the gap to the layer below, after ;TYPE: the line height
            close()
            height = val
            if cur is not None and cur.height == 0.0:
                cur.height = val
        elif kind == "type":
            close()
            typ = val
        elif kind == "width":
            close()
            width = val
        elif kind == "comment":
            if val.startswith("; CP TOOLCHANGE UNLOAD"):
                in_toolchange, tc_lines = True, []
            elif val.startswith("; CP TOOLCHANGE WIPE"):
                in_toolchange = False
                if toolchange_extra is None:
                    toolchange_extra = tc_lines
                close()
                block += 1
                tower_sec = "wipe"
            elif val.startswith("; WIPE_TOWER_END") or val.startswith("; CP EMPTY GRID END"):
                close()
                tower_sec = "wall"
            elif val.startswith("; CP EMPTY GRID START"):
                close()
                tower_sec = "grid"
        elif kind == "cmd":
            w = val.split()[0].upper()
            if w in ("M106", "M107"):
                close()
                _fan(fans, w, val)
                if in_toolchange:
                    tc_lines.append(val)
            elif w == "EXCLUDE_OBJECT_START":
                close()
                obj = val.split("NAME=", 1)[1] if "NAME=" in val else None
            elif w == "EXCLUDE_OBJECT_END":
                close()
                obj = None
            elif w in ("M104", "M109"):     # nozzle: kept per path, the writer sets it before each
                close()
                temp = _temp(val, tool, temp)
            elif w in TEMPS:
                close()
                cur.cmds.append(val)
            elif w in STATE:
                close()
                state[_state_key(w, val)] = val
            elif w == "SET_VELOCITY_LIMIT" and (extra := {k.upper(): v for k, v in re.findall(r"(\w+)=(\S+)", val)
                                                          if k.upper() not in ("ACCEL", "SQUARE_CORNER_VELOCITY")}):
                close()
                vlim = tuple(sorted({**dict(vlim), **extra}.items()))
            elif w in DROP:
                pass
            elif in_toolchange:
                tc_lines.append(val)
            elif w not in ("G4", "M400"):
                close()
                cur.cmds.append(val)
                prog.unknown[w] += 1
    close()
    prog.toolchange_extra = toolchange_extra or []
    prog.first_temp = next((p.temp for L in layers for p in L.paths), None)

    # layer Z / height when the slicer did not say
    prev = 0.0
    for L in layers:
        if L.z == 0.0 and L.paths:
            L.z = min(p.z for p in L.paths)
        if L.height == 0.0:
            L.height = round(L.z - prev, 4) if L.z > prev else first_setting(settings, "layer_height", 0.2)
        prev = L.z
    layers = [L for L in layers if L.paths or L.cmds]
    for k, L in enumerate(layers):
        L.index = k
    prog.layers = layers
    for L in layers:
        for p in L.paths:
            if p.tool is not None:
                prog.fans.setdefault((p.tool, L.index, p.type), p.fans)
                rows = prog.states.setdefault((p.tool, p.type), [])
                if not rows or rows[-1][0] != L.index:
                    rows.append((L.index, p.state))
    for t in {p.tool for L in layers for p in L.paths if p.tool is not None}:
        col = [next((p.temp for p in L.paths if p.tool == t and p.temp is not None), None) for L in layers]
        for order in (range(len(col)), range(len(col) - 1, -1, -1)):     # fill from below, then from above
            last = None
            for k in order:
                col[k] = last = col[k] if col[k] is not None else last
        prog.temps[t] = col

    # line width when the slicer did not write it: filament volume / (length * layer height)
    diam = prog.setting_list("filament_diameter", default=[1.75])
    for L in layers:
        for p in L.paths:
            if p.width is None:
                length = p.length()
                d = diam[p.tool] if p.tool is not None and p.tool < len(diam) else diam[0]
                area = math.pi * d * d / 4
                p.width = round(sum(p.e) * area / max(length * (p.height or L.height), 1e-9), 3) if length > 0 else 0.4

    # travel style: the slicer's settings, else what the file itself does
    def common(vals, default):
        return Counter(vals).most_common(1)[0][0] if vals else default
    r_len, r_f = common(retracts, (0.8, 1800.0))
    prog.travel = {
        "retract": first_setting(settings, "retraction_length", r_len),
        "retract_f": first_setting(settings, "retraction_speed", r_f / 60) * 60,
        "deretract_f": (first_setting(settings, "deretraction_speed", 0) * 60) or common(deretracts, r_f),
        "z_hop": first_setting(settings, "z_hop", common(hops, 0.4)),
        "travel_f": first_setting(settings, "travel_speed", common(travels, 9000.0) / 60) * 60,
        "min_travel": first_setting(settings, "retraction_minimum_travel", 1.0),
    }
    return prog


def _state_key(word, line):
    """What a state command sets: its word and parameter names (SET_PRESSURE_ADVANCE ADVANCE= and SMOOTH_TIME= are
    separate settings)."""
    code = line.split(";")[0]
    names = re.findall(r"\b([A-Za-z_]+)\s*=", code) + re.findall(r"\s([A-Za-z])-?\.?\d", code)
    return word, tuple(sorted({n.upper() for n in names}))


def _temp(cmd, tool, current):
    """Nozzle target after an M104/M109 (one aimed at another tool with T leaves it)."""
    s = re.search(r"\bS(\d*\.?\d+)", cmd)
    t = re.search(r"\bT(\d+)", cmd)
    return float(s.group(1)) if s and (not t or int(t.group(1)) == tool) else current


def _fan(fans, word, s):
    p = re.search(r"\bP(\d+)", s)
    key = "" if not p or p.group(1) == "0" else "P" + p.group(1)
    sv = re.search(r"\bS(\d*\.?\d+)", s)
    fans[key] = 0 if word == "M107" else int(float(sv.group(1))) if sv else 255

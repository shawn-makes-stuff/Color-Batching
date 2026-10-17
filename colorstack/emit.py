"""Write a band plan back out as G-code.

Travels, retracts, Z hops and tool changes follow the file's own settings. Inside a band other regions may stand
higher than the current layer, so a travel that passes within the nozzle's reach of them lifts above them.
Extrusion is written relative; a file that started absolute gets M83 after its start G-code and M82 back
before its end G-code.
"""
import math
from dataclasses import replace

import numpy as np

from .gcode import BARRIERS
from .plan import Grid, densify

# a layer's bed / chamber changes and pauses wait until every layer below it is printed
SETTLED = {"M140", "M190", "M141", "M191"} | BARRIERS


def _fmt(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _print_time(paths):
    return sum(math.dist(p.pts[i], p.pts[i + 1]) / (p.f[i] / 60) for p in paths for i in range(len(p.e)) if p.f[i] > 0)


class Heights:
    """Tallest plastic printed so far, on a grid, for travels that lift only as high as they must."""

    def __init__(self, program, res=0.5):
        self.grid = Grid(program, res)
        self.h = np.zeros(self.grid.shape)
        self.pad = max(p.width for L in program.layers for p in L.paths) / 2 + res

    def add(self, p):
        r, c = self.grid.cells(densify(p, self.grid.res / 2)[0])
        np.maximum.at(self.h, (r, c), p.z)

    def max_near(self, a, b, radius):
        """Tallest plastic within radius of the segment a-b (line width and grid step added)."""
        g, radius = self.grid, radius + self.pad
        c0 = max(0, int((min(a[0], b[0]) - radius - g.x0) / g.res))
        c1 = min(g.shape[1], int((max(a[0], b[0]) + radius - g.x0) / g.res) + 2)
        r0 = max(0, int((min(a[1], b[1]) - radius - g.y0) / g.res))
        r1 = min(g.shape[0], int((max(a[1], b[1]) + radius - g.y0) / g.res) + 2)
        if c0 >= c1 or r0 >= r1:
            return 0.0
        px, py = np.meshgrid(g.x0 + np.arange(c0, c1) * g.res, g.y0 + np.arange(r0, r1) * g.res)
        dx, dy = b[0] - a[0], b[1] - a[1]
        n2 = dx * dx + dy * dy
        t = np.clip(((px - a[0]) * dx + (py - a[1]) * dy) / n2, 0, 1) if n2 > 0 else 0.0
        near = np.hypot(px - a[0] - t * dx, py - a[1] - t * dy) <= radius
        sub = self.h[r0:r1, c0:c1][near]
        return float(sub.max()) if sub.size else 0.0


class Writer:
    def __init__(self, program, nozzle, swap_lift=1.0, stacked_fan=True, lift_wait=False, min_layer_time=0.0,
                 tower_between=False, respect_min_layer_time=True):
        """min_layer_time > 0 overrides the file's. tower_between: print the tower between a colour's stacked
        layers (cooling time) instead of all right after the swap. respect_min_layer_time off: stacked regions
        keep the sliced speeds (the fan rule still applies)."""
        self.p = program
        self.respect_min_layer_time = respect_min_layer_time
        self.swap_lift = swap_lift
        self.stacked_fan = stacked_fan
        self.lift_wait = lift_wait
        self.tower_between = tower_between
        self.nozzle = nozzle
        self.heights = Heights(program)
        t = program.travel
        self.retract, self.retract_f, self.deretract_f = t["retract"], t["retract_f"], t["deretract_f"]
        self.z_hop, self.travel_f, self.min_travel = t["z_hop"], t["travel_f"], t["min_travel"]
        setting = program.setting_list
        diam = setting("filament_diameter", default=[1.75])
        self.area = [math.pi * d * d / 4 for d in diam]
        n = len(diam)
        flat = setting("flush_volumes_matrix")
        self.flush = [flat[i * n:(i + 1) * n] for i in range(n)] if flat and len(flat) == n * n else None
        self.flush_mult = setting("flush_multiplier", default=[1.0])[0]
        self.nozzle_t = program.first_temp
        self.left = []             # paths still to print, per layer index (write counts them)
        self.settled = 0           # layers below are all printed
        self.waiting = {}          # layer index -> its SETTLED commands
        # cooling (Orca/Bambu or PrusaSlicer names)
        self.min_layer_time = setting("slow_down_layer_time") or setting("slowdown_below_layer_time")
        on = setting("slow_down_for_layer_cooling") or setting("cooling")     # per filament, Orca / Prusa
        if self.min_layer_time and on:
            self.min_layer_time = [t if on[min(k, len(on) - 1)] else 0.0 for k, t in enumerate(self.min_layer_time)]
        self.min_layer_source = "file" if self.min_layer_time else "none"
        if min_layer_time > 0:
            self.min_layer_time = [float(min_layer_time)] * n
            self.min_layer_source = "override"
        self.min_speed = setting("slow_down_min_speed") or setting("min_print_speed")
        self.fan_min = setting("fan_min_speed")
        self.fan_max = setting("fan_max_speed")
        self.fan_layer_time = setting("fan_cooling_layer_time")
        self.no_fan_layers = int(setting("close_fan_the_first_x_layers", default=[0])[0])
        self.slowed = self.short = self.fanned = self.waited = 0
        # wipe while retracting, as the slicer does
        self.wipe = bool(setting("wipe", default=[0])[0])
        self.wipe_dist = setting("wipe_distance", default=[0.0])[0]
        ws = program.settings.get("wipe_speed", "100%").split(",")[0].strip()
        try:
            self.wipe_f = (self.travel_f * float(ws.rstrip("%")) / 100) if ws.endswith("%") else float(ws) * 60
        except ValueError:
            self.wipe_f = self.travel_f
        rb = program.settings.get("retract_before_wipe", "0%").split(",")[0].strip().rstrip("%")
        try:
            self.before_wipe = float(rb) / 100
        except ValueError:
            self.before_wipe = 0.0
        self.role_wipe = program.settings.get("role_based_wipe_speed", "0").strip() == "1"   # Orca: wipe at print speed
        # the slicer's tool change extras, minus the part fan (fans follow each path)
        self.tc_extra = [ln for ln in program.toolchange_extra if not (ln.startswith("M106") and " P" not in ln)]
        self.out = []
        self.pos = None
        self.z = None
        self.top = 0.0             # tallest material printed so far
        self.f = None
        self.accel = None
        self.scv = None
        self.vlim = ()
        self.fans = {}
        self.obj = None
        self.type = None
        self.width = None
        self.height = None
        self.tool = program.first_tool
        self.last = None           # last path written
        self.last_pts = None       # its points, for the wipe
        self.object_f = None       # feed of the last object (not tower) line: Orca's role-based wipe speed
        self.retracted = False
        self.touched = set()

    def emit(self, line):
        self.out.append(line)

    def purge_length(self, a, b):
        if self.flush is None or max(a, b) >= len(self.flush):
            return 0.0
        return self.flush[a][b] * self.flush_mult / self.area[min(b, len(self.area) - 1)]

    def retract_move(self):
        """Retract like the slicer: part in place, the rest wiping back along the last line (planar lines only:
        a wipe along a ramp could drag into its higher part). As Orca's Wipe: at most retraction speed x wipe
        length / wipe speed is retracted during the wipe, the rest before it."""
        if self.retracted:
            return
        self.retracted = True
        pts = self.last_pts
        if not (self.wipe and self.wipe_dist > 0 and pts and len(pts) > 1 and self.pos is not None
                and math.dist(self.pos, pts[-1]) < 1e-6):
            self.emit(f"G1 E-{_fmt(self.retract)} F{_fmt(self.retract_f)}")
            return
        back = list(reversed(pts))
        route, left = [], self.wipe_dist
        for a, b in zip(back, back[1:]):
            d = math.dist(a, b)
            if d <= 0:
                continue
            if d >= left:
                t = left / d
                route.append(((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), left))
                break
            route.append((b, d))
            left -= d
        total = sum(d for _, d in route)
        wipe_f = max(self.object_f if self.role_wipe and self.object_f else self.wipe_f, 600.0)  # Orca: >= 10 mm/s
        before = self.retract * self.before_wipe
        during = min(self.retract - before, self.retract_f * total / wipe_f)
        before = self.retract - during
        if before > 1e-6:
            self.emit(f"G1 E-{before:.5f} F{_fmt(self.retract_f)}")
        if during <= 1e-6:
            return
        self.emit(";WIPE_START")
        for (x, y), d in route:
            self.emit(f"G1 X{_fmt(x)} Y{_fmt(y)} E-{during * d / total:.5f} F{_fmt(wipe_f)}")
        self.emit(";WIPE_END")
        self.pos, self.f = route[-1][0], wipe_f
        self.last_pts = None

    def deretract(self):
        if self.retracted:
            self.emit(f"G1 E{_fmt(self.retract)} F{_fmt(self.deretract_f)}")
            self.retracted = False
            self.f = self.deretract_f

    def lift(self, z):
        if self.z is None or abs(z - self.z) > 1e-6:
            self.emit(f"G1 Z{_fmt(z)} F{_fmt(self.travel_f)}")
            self.z = z
            self.f = self.travel_f

    def _move_to(self, x, y, z):
        self.emit(f"G1 X{_fmt(x)} Y{_fmt(y)} F{_fmt(self.travel_f)}")
        self.f = self.travel_f
        self.lift(z)
        self.pos = (x, y)
        self.deretract()

    def clear_z(self, x, y, z):
        """Height a travel from here to (x, y) at z must clear: the tallest plastic within the nozzle's reach on the
        way (its cone at the tallest printed point reaches no further; past clear_height the heater block could,
        so then the tallest printed anywhere)."""
        base = min(z, self.z) if self.z is not None else z
        if self.pos is None or self.top <= base + 1e-6 or self.top - base > self.nozzle.clear_height:
            return self.top
        return self.heights.max_near(self.pos, (x, y), self.nozzle.reach(self.top - base))

    def travel(self, x, y, z, orca=None, high=None):
        """orca = (retract, hop): the slicer's own choice, when this is the travel it planned between the same two
        paths and nothing taller is in the way. high: the height to clear (clear_z)."""
        high = self.top if high is None else high
        if orca is not None and self.pos is not None and self.z is not None:
            retract, hop = orca
            if retract:
                self.retract_move()
            if hop or abs(z - self.z) > 1e-6:
                self.lift(max(z, self.z) + (self.z_hop if hop else 0.0))
            self._move_to(x, y, z)
            return
        if self.pos is not None and self.z is not None and abs(z - self.z) < 1e-6:
            d = math.dist(self.pos, (x, y))
            if d < 1e-4:
                return
            if d <= self.min_travel and high <= z + 1e-6:
                self._move_to(x, y, z)
                return
        self.retract_move()
        self.lift(max(high, z, self.z or 0.0) + self.z_hop)
        self._move_to(x, y, z)

    def _end_object(self):
        if self.obj is not None:
            self.emit("EXCLUDE_OBJECT_END NAME=" + self.obj)
            self.obj = None

    def set_temp(self, t):
        """Nozzle target t (None: unknown, leave it) without waiting, as the slicer changes it between layers."""
        if t is not None and t != self.nozzle_t:
            self._outside([f"M104 S{_fmt(t)}"])
            self.nozzle_t = t

    def change_tool(self, tool, temp=None):
        """Swap to tool, heading for temp (its temperature on the first layer it prints) during the swap."""
        if tool == self.tool:
            return
        self._end_object()
        self.retract_move()
        # the printer's swap routine travels to the purge tray from here: start above everything printed
        self.lift(max(self.top, self.z or 0.0) + max(self.z_hop, self.swap_lift))
        self.emit(f"; COLORSTACK TOOLCHANGE T{self.tool} -> T{tool}")
        self.set_temp(temp)
        self.emit(self.p.tool_format.format(n=tool, purge=f"{self.purge_length(self.tool, tool):.3f}"))
        self.out.extend(self.tc_extra)
        self.tool = tool
        self.last = None
        # the swap moves the head, may touch fans, and comes back primed
        self.pos = None
        self.z = None
        self.fans = {}
        self.retracted = False

    def layer_start(self, layer):
        if layer.index in self.touched:
            return
        self.touched.add(layer.index)
        self._outside([c for c in layer.cmds if c.split()[0].upper() not in SETTLED])
        later = [c for c in layer.cmds if c.split()[0].upper() in SETTLED]
        if later:
            self.waiting[layer.index] = later
        self._settle()
        if self.p.stats_info:
            self._outside([f"SET_PRINT_STATS_INFO TOTAL_LAYER={len(self.p.layers)} CURRENT_LAYER={layer.index + 1}"])

    def _outside(self, lines):
        """Layer commands go between objects, as the slicer writes them at a layer change."""
        if lines:
            self._end_object()
            self.out.extend(lines)

    def _settle(self):
        """Release the SETTLED commands of layers whose layers below are all printed."""
        while self.settled < len(self.left) and self.left[self.settled] == 0:
            self.settled += 1
        for k in sorted(k for k in self.waiting if k <= self.settled):
            self._outside(self.waiting.pop(k))

    def own_fans(self, p, tool, layer):
        """A piece handed to another tool gets the fans the slicer set for that tool's lines of its kind there."""
        if p.tool is None or p.tool == tool:
            return p
        fans = self.p.fans.get((tool, layer.index, p.type))
        return p if fans is None or fans == p.fans else replace(p, fans=fans)

    def path(self, p, layer):
        # the loaded filament's temperature for this layer (a handed-over piece is printed by another tool than it
        # was sliced with); tower lines at whatever the loaded filament is at
        if p.tool is not None:
            self.set_temp(self.p.temp_at(self.tool, layer.index))
        if p.obj != self.obj:
            self._end_object()
            if p.obj is not None:
                self.emit("EXCLUDE_OBJECT_START NAME=" + p.obj)
            self.obj = p.obj
        x0, y0 = p.pts[0]
        q = self.last
        z0 = p.z
        high = self.clear_z(x0, y0, z0)
        if q is not None and high <= max(p.z, q.z) + 1e-6:
            # nothing taller on the way: travel as the slicer did into this path (its retract and hop). Where the
            # order changed: retract too if the travel is longer than the slicer's minimum, and no hop between two
            # tower pieces (the slicer never hops inside its tower)
            moved = not (q.tail and p.head and p.seq == q.seq + 1 and p.tool == q.tool)
            far = self.pos is not None and math.dist(self.pos, (x0, y0)) > self.min_travel
            in_tower = p.tower is not None and q.tower is not None
            self.travel(x0, y0, z0, (p.retract_before or (moved and far), p.hop_before and not (moved and in_tower)), high)
        else:
            self.travel(x0, y0, z0, None, high)
        if p.accel is not None and p.accel != self.accel:
            self.emit(self.p.accel_format.format(a=_fmt(p.accel)))
            self.accel = p.accel
        if p.scv is not None and p.scv != self.scv:
            self.emit(f"SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY={_fmt(p.scv)}")
            self.scv = p.scv
        if p.vlim and p.vlim != self.vlim:      # () = nothing set yet: the printer's own, nothing to restore
            self.emit("SET_VELOCITY_LIMIT " + " ".join(f"{k}={v}" for k, v in p.vlim))
            self.vlim = p.vlim
        for k, v in p.fans.items():
            if self.fans.get(k) != v:
                self.emit(f"M106 {k + ' ' if k else ''}S{v}")
                self.fans[k] = v
        if p.type != self.type:
            self.emit(";TYPE:" + p.type)
            self.type = p.type
        if p.width != self.width:
            self.emit(f";WIDTH:{_fmt(p.width)}")
            self.width = p.width
        height = p.height or layer.height
        if height != self.height:
            self.emit(f";HEIGHT:{_fmt(height)}")
            self.height = height
        for i, (e, f) in enumerate(zip(p.e, p.f)):
            x, y = p.pts[i + 1]
            fs = f" F{_fmt(f)}" if f != self.f else ""
            es = f" E{e:.5f}" if e > 0 else ""
            self.emit(f"G1 X{_fmt(x)} Y{_fmt(y)}{es}{fs}")
            self.f = f
        self.pos = p.pts[-1]
        self.last_pts = p.pts
        if p.tower is None:
            self.object_f = p.f[-1]
        self.top = max(self.top, p.z)
        self.heights.add(p)
        self.last = p
        self.left[layer.index] -= 1
        if self.left[layer.index] == 0:
            self._settle()

    def _cool(self, paths, tool, layer_index, extra=0.0):
        """A region printed again straight on top of itself still gets the file's minimum layer time: slow it
        like the slicer does (not below its minimum speed) and raise the fan by its rule. extra: seconds of other
        printing before the next layer (the tower, with tower_between). Returns (paths, seconds still short)."""
        if not self.min_layer_time or not paths:
            return paths, 0.0
        k = tool if tool is not None and tool < len(self.min_layer_time) else 0
        need = self.min_layer_time[k]
        floor = (self.min_speed[min(k, len(self.min_speed) - 1)] if self.min_speed else 0.0) * 60
        t = _print_time(paths)
        if need <= 0 or t <= 0:
            return paths, 0.0
        if t + extra >= need or not self.respect_min_layer_time:
            return self._fan(paths, k, t + extra, layer_index), 0.0
        factor = t / (need - extra)
        out = [replace(p, f=[max(f * factor, min(f, floor)) for f in p.f]) for p in paths]
        t2 = _print_time(out) + extra
        self.slowed += 1
        if t2 < need - 0.05:
            self.short += 1
        return self._fan(out, k, t2, layer_index), max(0.0, need - t2)

    def _fan(self, paths, k, layer_time, layer_index):
        """Orca CoolingBuffer: max fan below slow_down_layer_time, blended to min fan at fan_cooling_layer_time.
        Never lowers what the slicer set."""
        if not (self.stacked_fan and self.fan_max and self.fan_min and self.fan_layer_time):
            return paths
        if layer_index < self.no_fan_layers:
            return paths

        def pick(lst):
            return lst[min(k, len(lst) - 1)]
        lo, hi = pick(self.fan_min), pick(self.fan_max)
        slow, full = pick(self.min_layer_time), pick(self.fan_layer_time)
        if layer_time < slow:
            pct = hi
        elif layer_time < full and full > slow:
            t = (layer_time - slow) / (full - slow)
            pct = math.floor(t * lo + (1 - t) * hi + 0.5)
        else:
            return paths
        s = int(round(pct * 255 / 100))
        out = []
        for p in paths:
            if p.fans.get("", 0) < s:
                p = replace(p, fans={**p.fans, "": s})
                self.fanned += 1
            out.append(p)
        return out

    def wait(self, seconds):
        """Lift clear and wait out the rest of the minimum layer time, still retracted."""
        self.retract_move()
        self.lift(max(self.top, self.z or 0.0) + max(self.z_hop, self.swap_lift))
        self.emit(f"G4 P{int(round(seconds * 1000))} ; ColorStack: rest of the minimum layer time")
        self.waited += 1

    def write(self, bands, summary_lines):
        """The whole file, as a list of lines."""
        out = self.out
        out.extend(self.p.preamble)
        out.extend("; " + s for s in summary_lines)
        if self.p.abs_e:
            self.emit("M83 ; ColorStack writes relative extrusion")
        self.left = [0] * len(self.p.layers)
        for band in bands:
            for layer, w in zip(band.layers, band.work):
                self.left[layer.index] += len(w)
        for band in bands:
            for unit in band.order:
                items = [[self.own_fans(p, unit[0], layer) for owner, p in w if owner == unit]
                         for layer, w in zip(band.layers, band.work)]
                parts = [[p for p in ps if p.tower is None] for ps in items]
                first_layer = next((layer.index for layer, ps in zip(band.layers, parts) if ps), None)
                self.change_tool(unit[0], None if first_layer is None else self.p.temp_at(unit[0], first_layer))
                tool = unit[0]
                # tower right after the swap; with tower_between only the wipe, walls and grid between layers
                late = ("wall", "grid") if self.tower_between else ()
                first = [[p for p in ps if p.tower is not None and p.tower not in late] for ps in items]
                between = [[p for p in ps if p.tower in late] for ps in items]
                for layer, ps in zip(band.layers, first):
                    if ps:
                        self.layer_start(layer)
                    for p in ps:
                        self.path(p, layer)
                for j, layer in enumerate(band.layers):
                    mine, rest = parts[j], between[j]
                    short = 0.0
                    if mine or rest:
                        self.layer_start(layer)
                    if mine and j + 1 < len(parts) and parts[j + 1]:
                        mine, short = self._cool(mine, tool, layer.index, _print_time(rest))
                    for p in mine + rest:
                        self.path(p, layer)
                    if short > 0.05 and self.lift_wait:
                        self.wait(short)
            for layer in band.layers:      # a layer with no extrusion still gets its commands
                self.layer_start(layer)
        self._end_object()
        for k in sorted(self.waiting):     # left over only if a layer never settled (nothing below it printed)
            self._outside(self.waiting.pop(k))
        if not self.retracted:
            self.emit(f"G1 E-{_fmt(self.retract)} F{_fmt(self.retract_f)}")
        self.lift(self.top + self.z_hop)
        if self.p.abs_e:
            self.emit("G92 E0")
            self.emit("M82 ; back to the file's absolute extrusion for its end G-code")
        out.extend(self.p.postamble)
        return out

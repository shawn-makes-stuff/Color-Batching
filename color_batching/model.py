"""The parsed print: layers of extrusion paths, with the slicer's settings and the state each path printed under."""
import math
from collections import Counter
from dataclasses import dataclass, field

VISIBLE_TYPES = {"Outer wall", "Overhang wall", "Top surface", "Bottom surface", "Ironing"}

NEUTRAL_TYPES = {"Prime tower"}  # printed by whichever tool is loaded

# pause / filament change: everything below must be printed first, nothing above, so no band crosses the layer
BARRIERS = {"M600", "M601", "M0", "M1", "M25", "M125", "PAUSE"}


@dataclass
class Path:
    tool: int | None          # None = prime tower
    type: str
    width: float
    z: float
    pts: list                 # [(x, y), ...], one more than segments
    e: list                   # filament mm per segment
    f: list                   # mm/min per segment
    accel: float | None
    fans: dict                # {"": S, "P2": S, ...} as set before the path
    obj: str | None           # EXCLUDE_OBJECT name
    seq: int = 0              # index in the slicer's path order
    retract_before: bool = True
    hop_before: bool = True
    head: bool = True         # starts where the slicer's path started (False for a later piece of a split)
    tail: bool = True
    tower: str | None = None  # prime tower part: "wipe", "grid" or "wall"
    block: int = -1           # wipe block number (one per tool change)
    scv: float | None = None  # Klipper square corner velocity set before the path
    temp: float | None = None  # nozzle temperature the slicer printed it at
    vlim: tuple = ()          # other SET_VELOCITY_LIMIT parameters in force, ((name, value), ...)
    height: float | None = None   # line height the slicer wrote for it (;HEIGHT: after ;TYPE:), None = the layer's
    state: tuple = ()         # machine state commands in force (PA, speed / flow factor, jerk): ((key, line), ...)
    zs: tuple | None = None   # Z per point where it changes along the path (Orca scarf seam ramp); z is then the top
    lead: tuple = ()          # the slicer's way into the path since the extrusion before it: ("move", x, y, z, de, f)
                              # (retracts, wipes, lifts, travel route), ("line", text) (G10 / G11, wipe markers) and
                              # ("accel", a) (the slicer's travel / wipe acceleration)

    @property
    def visible(self):
        return self.type in VISIBLE_TYPES

    def length(self):
        return sum(math.dist(self.pts[k], self.pts[k + 1]) for k in range(len(self.e)))

    @property
    def z0(self):
        """Z at the first point (a scarf seam ramp starts below the layer)."""
        return self.zs[0] if self.zs else self.z

    @property
    def z1(self):
        return self.zs[-1] if self.zs else self.z


@dataclass
class Layer:
    index: int
    z: float
    height: float
    paths: list = field(default_factory=list)
    cmds: list = field(default_factory=list)   # emitted at the layer's start

    @property
    def barrier(self):
        return any(c.split()[0].upper() in BARRIERS for c in self.cmds)


@dataclass
class Program:
    settings: dict            # the slicer's "; key = value" config block
    preamble: list
    postamble: list
    layers: list
    first_tool: int
    header: list
    tool_format: str = "T{n}"     # the file's own tool change line, {purge} for PURGE_LENGTH
    abs_e: bool = False           # start G-code leaves M82
    stats_info: bool = False      # file uses SET_PRINT_STATS_INFO
    accel_format: str = "SET_VELOCITY_LIMIT ACCEL={a}"
    first_temp: float | None = None   # nozzle temperature when the layers start
    temps: dict = field(default_factory=dict)    # tool -> nozzle temperature per layer index (see temp_at)
    fans: dict = field(default_factory=dict)     # (tool, layer index, type) -> fans the slicer set for it
    states: dict = field(default_factory=dict)   # (tool, type) -> [(layer index, state)] the slicer printed it under
    travel: dict = field(default_factory=dict)   # retract, retract_f, deretract_f, z_hop, travel_f, min_travel
    unknown: Counter = field(default_factory=Counter)
    # what the slicer writes in a swap: tool -> lines before T when leaving it (filament end G-code), lines after T
    # when entering it (filament start G-code); the first swap of each
    # (object, type) of support (base or interface) printed with whatever filament was loaded (Orca support_filament /
    # support_interface_filament "Default": the slicer printed it with several filaments); support always printed with
    # one filament keeps it
    free_support: frozenset = frozenset()
    toolchange_end: dict = field(default_factory=dict)
    toolchange_start: dict = field(default_factory=dict)

    def temp_at(self, tool, index):
        """Nozzle temperature for tool printing layer index: what the slicer printed that tool at there (a layer
        the tool had nothing on takes the nearest one below, else above). None if unknown."""
        col = self.temps.get(tool)
        return col[index] if col else None

    def state_at(self, tool, index, typ):
        """Machine state the slicer printed tool's lines of type typ under at layer index (the nearest layer that has
        them; any type of the tool if it never printed that type). None if the tool prints nothing."""
        rows = self.states.get((tool, typ)) or [r for (t, _), rs in self.states.items() if t == tool for r in rs]
        return min(rows, key=lambda r: abs(r[0] - index))[1] if rows else None

    def setting_list(self, key, cast=float, default=None):
        if key not in self.settings:
            return default
        try:
            return [cast(v) for v in self.settings[key].split(",")]
        except ValueError:
            return default

    def flush_length(self, a, b):
        """Filament mm Orca flushes changing from tool a to b ({flush_length}): flush_volumes_matrix x
        flush_multiplier over the new filament's area; 0 without a matrix."""
        diam = self.setting_list("filament_diameter", default=[1.75])
        flat = self.setting_list("flush_volumes_matrix")
        n = len(diam)
        if not flat or len(flat) != n * n or max(a, b) >= n:
            return 0.0
        return flat[a * n + b] * self.setting_list("flush_multiplier", default=[1.0])[0] / (math.pi * diam[b] * diam[b] / 4)


def first_setting(settings, key, default):
    """First value of a per-filament / per-mode setting."""
    try:
        return float(settings[key].split(",")[0])
    except (KeyError, ValueError):
        return default
